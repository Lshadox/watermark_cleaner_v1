"""Synthetic checks for tracking, image repair, video and audio output."""
from pathlib import Path
import tempfile
import subprocess
import cv2
import numpy as np
import cleaner


def run():
    assert cleaner.interpolate_box(5, [(0, (1, 2, 20, 10)),
                                       (10, (11, 12, 30, 20))]) == (6, 7, 25, 15)
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        rng = np.random.default_rng(7)
        logo = rng.integers(30, 250, (16, 30, 3), dtype=np.uint8)
        frames = []
        for i in range(12):
            frame = np.full((120, 160, 3), 80, np.uint8)
            frame[40:56, 20+i*3:50+i*3] = logo
            frames.append(frame)
        box = (20, 40, 30, 16)
        template = cleaner.features(frames[0])[40:56, 20:50].copy()
        matched, score = cleaner.locate(frames[5], template, box)
        assert matched == (35, 40, 30, 16), (matched, score)
        repaired = cleaner.repair(frames[0], box)
        assert abs(float(repaired[45:50, 25:45].mean())-80) < 5
        image = base / '中文图片.png'
        cleaner.write_image(image, repaired)
        assert np.array_equal(cleaner.read_image(image), repaired)
        originals = base / 'originals'
        originals.mkdir()
        images = []
        for i, item in enumerate((frames[0], frames[8],
                cv2.resize(frames[4], (320, 240)),
                np.full((12, 15, 3), 80, np.uint8))):
            path = originals / f'batch_{i}.png'
            cleaner.write_image(path, item)
            images.append(path)
        broken = originals / 'broken.png'
        broken.write_bytes(b'not an image')
        images.append(broken)
        snapshots = [p.read_bytes() for p in images]
        records, report = cleaner.process_images(images, frames[0], box, base/'batch')
        assert [r['status'] for r in records] == [
            'cleaned', 'cleaned', 'cleaned', 'unmatched', 'error'], records
        assert records[1]['box'][:2] == [44, 40], records[1]
        assert report.exists() and [p.read_bytes() for p in images] == snapshots
        unmatched = base/'batch'/'未匹配'/images[3].name
        assert unmatched.read_bytes() == snapshots[3]
        assert records[3]['output'] == str(unmatched)
        # Same name from two folders must preserve both files without overwriting.
        other = base/'other'
        other.mkdir()
        duplicate = other/images[3].name
        duplicate.write_bytes(images[3].read_bytes())
        duplicate_records, _ = cleaner.process_images(
            [images[0], images[3], duplicate], frames[0], box, base/'duplicates')
        assert all(r['status'] == 'unmatched' for r in duplicate_records[1:])
        copied = [Path(r['output']) for r in duplicate_records[1:]]
        assert copied[0] != copied[1]
        assert all(p.read_bytes() == snapshots[3] for p in copied)
        cancelled, cancel_report = cleaner.process_images(images, frames[0], box,
            base/'cancelled', cancelled=lambda: True)
        assert cancelled == [] and cancel_report.exists()
        source = base / 'input.mp4'
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'),
                                 12, (160, 120))
        assert writer.isOpened()
        for frame in frames:
            writer.write(frame)
        writer.release()
        audio_source = base / 'audio_input.mp4'
        result = subprocess.run([cleaner.imageio_ffmpeg.get_ffmpeg_exe(), '-y',
            '-i', str(source), '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
            '-c:v', 'copy', '-c:a', 'aac', '-shortest', str(audio_source)],
            capture_output=True)
        assert result.returncode == 0, result.stderr
        for mode in ('template', 'keyframes'):
            capture = cv2.VideoCapture(str(audio_source))
            ok, first = capture.read()
            assert ok
            output = base / (mode+'.mp4')
            try:
                missed, report = cleaner.process_video(audio_source, output, first,
                    capture, box, keyframes=[(0, box), (11, (53, 40, 30, 16))]
                    if mode == 'keyframes' else None, show_preview=False)
            finally:
                capture.release()
            assert output.stat().st_size > 1000 and report.exists()
            verify = cv2.VideoCapture(str(output))
            assert int(verify.get(cv2.CAP_PROP_FRAME_COUNT)) == 12
            verify.release()
            probe = subprocess.run([cleaner.imageio_ffmpeg.get_ffmpeg_exe(),
                '-i', str(output), '-map', '0:a:0', '-t', '0.1', '-f', 'null', '-'],
                capture_output=True)
            assert probe.returncode == 0, probe.stderr
            assert missed == 0, missed
        print('PASS: batch images, moved/scaled logos, unmatched and corrupt images, '
              'cancellation, originals preserved, keyframes, video and audio')


if __name__ == '__main__':
    run()
