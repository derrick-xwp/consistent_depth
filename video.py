#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.

import cv2
import logging
import os
from os.path import join as pjoin
import sys
import tempfile
from utils import (frame_sampling, image_io)
from utils.helpers import mkdir_ifnotexists


ffmpeg = "ffmpeg"
ffprobe = "ffprobe"


def sample_pairs(frame_range, flow_ops):
    #TODO: update the frame range with reconstruction range
    name_mode_map = frame_sampling.SamplePairsMode.name_mode_map()
    opts = [
        frame_sampling.SamplePairsOptions(mode=name_mode_map[op]) for op in flow_ops
    ]
    pairs = frame_sampling.SamplePairs.sample(
        opts, frame_range=frame_range, two_way=True
    )
    print(f"Sampled {len(pairs)} frame pairs.")
    return pairs


class Video:
    def __init__(self, path, video_file=None):
        self.path = path
        self.video_file = video_file

    def check_extracted_pts(self):
        pts_file = "%s/frames.txt" % self.path
        if not os.path.exists(pts_file):
            return False
        with open(pts_file, "r") as file:
            lines = file.readlines()
            self.frame_count = int(lines[0])
            width = int(lines[1])
            height = int(lines[2])
            print("%d frames detected (%d x %d)." % (self.frame_count, width, height))
            if len(lines) != self.frame_count + 3:
                sys.exit("frames.txt has wrong number of lines")
            print("frames.txt exists, checked OK.")
            return True
        return False

    def extract_pts(self):
        if self.check_extracted_pts():
            # frames.txt exists and checked OK.
            return

        if not os.path.exists(self.video_file):
            sys.exit("ERROR: input video file '%s' not found.", self.video_file)  # 如果视频文件没有找到报错

        # Get width and height
        tmp_file = tempfile.mktemp(".png")
        cmd = "%s -i %s -vframes 1 %s" % (ffmpeg, self.video_file, tmp_file)  # 此命令的作用为输出视频第1帧
        print(cmd)
        res = os.popen(cmd).read()  # 执行命令行 并获得屏幕输出 ffmpeg -i 视频文件路径 -vframes 1 临时文件
        image = image_io.load_image(tmp_file)
        height = image.shape[0]  # 获得视频的高
        width = image.shape[1]  #  获得视频的宽度
        os.remove(tmp_file)
        if os.path.exists(tmp_file):  # 检查临时文件是否存在
            sys.exit("ERROR: unable to remove '%s'" % tmp_file)  # 报告无法删除临时文件

        # Get PTS
        def parse_line(line, token):
            if line[: len(token)] != token:
                sys.exit("ERROR: record is malformed, expected to find '%s'." % token)
            return line[len(token) :]

        ffprobe_cmd = "%s %s -select_streams v:0 -show_frames" % (
            ffprobe,
            self.video_file,
        )  # ffprobe 视频地址 -select_streams v:0 -show_frames
        cmd = ffprobe_cmd + " | grep pkt_pts_time"
        print(cmd)
        res = os.popen(cmd).read()
        pts = []
        for line in res.splitlines():
            pts.append(parse_line(line, "pkt_pts_time="))  # 获得PTS（Presentation Time Stamp）
            # https: // zhuanlan.zhihu.com / p / 34162672
        self.frame_count = len(pts)
        print("%d frames detected." % self.frame_count)

        pts_file = "%s/frames.txt" % self.path
        with open(pts_file, "w") as file:  # 将得到的pts写入文件
            file.write("%d\n" % len(pts))
            file.write("%s\n" % width)
            file.write("%s\n" % height)
            for t in pts:
                file.write("%s\n" % t)

        self.check_extracted_pts() #检查frames.txt 文件是否正确

    def check_frames(self, frame_dir, extension, frames=None):
        if not os.path.isdir(frame_dir):
            return False

        files = os.listdir(frame_dir)
        files = [n for n in files if n.endswith(extension)]  # 获得当前目录下的所有图片
        if len(files) == 0:
            return False

        if frames is None:
            frames = range(self.frame_count)  # frame_count等于帧数 self.frame_count = len(pts)

        if len(files) != len(frames): # 找到的frames图像与pts数目不符合
            sys.exit(
                "ERROR: expected to find %d files but found %d in '%s'"
                % (self.frame_count, len(files), frame_dir)
            )
        for i in frames:
            frame_file = "%s/frame_%06d.%s" % (frame_dir, i, extension)  #依次检查是否齐全 视频文件目录/color_full/frame_%06d.pmg
            if not os.path.exists(frame_file):
                sys.exit("ERROR: did not find expected file '%s'" % frame_file)
        print("Frames found, checked OK.")

        return True

    def extract_frames(self):
        frame_dir = "%s/color_full" % self.path
        mkdir_ifnotexists(frame_dir)  # 创建目录 视频文件目录/color_full

        if self.check_frames(frame_dir, "png"):  # 用于检查视频文件目录/color_full否已经有frames文件，如果有则不需要再提取
            # Frames are already extracted and checked OK.
            return

        if not os.path.exists(self.video_file):
            sys.exit("ERROR: input video file '%s' not found.", self.video_file)

        cmd = "%s -i %s -start_number 0 -vsync 0 %s/frame_%%06d.png" % (
            ffmpeg,
            self.video_file,
            frame_dir,
        )  # ffmpeg -i 视频文件 -start_number 0 -vsync 0 视频文件目录/color_full/frame_%%06d.png
        print(cmd)
        os.popen(cmd).read()

        count = len(os.listdir(frame_dir))
        if count != self.frame_count:
            sys.exit(
                "ERROR: %d frames extracted, but %d PTS entries."  # 提取到的帧数与记录的pts数目不一致
                % (count, self.frame_count)
            )

        self.check_frames(frame_dir, "png")

    def downscale_frames(
        self, subdir, max_size, ext, align=16, full_subdir="color_full"
    ):
        full_dir = pjoin(self.path, full_subdir)
        down_dir = pjoin(self.path, subdir)

        mkdir_ifnotexists(down_dir)

        if self.check_frames(down_dir, ext):
            # Frames are already extracted and checked OK.
            return

        for i in range(self.frame_count):
            full_file = "%s/frame_%06d.png" % (full_dir, i)
            down_file = ("%s/frame_%06d." + ext) % (down_dir, i)
            suppress_messages = (i > 0)
            image = image_io.load_image(
                full_file, max_size=max_size, align=align,
                suppress_messages=suppress_messages
            )
            image = image[..., ::-1]  # Channel swizzle

            if ext == "raw":
                image_io.save_raw_float32_image(down_file, image)
            else:
                cv2.imwrite(down_file, image * 255)

        self.check_frames(down_dir, ext)
