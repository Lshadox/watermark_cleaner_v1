"""Local image repair and moving-logo tracker. Python 3.10+."""
from pathlib import Path
import json
import math
import subprocess
import tempfile
import queue
import threading
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
import numpy as np
import imageio_ffmpeg


IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}


def read_image(path):
    frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('无法读取图片。')
    return frame


def write_image(path, frame):
    ok, encoded = cv2.imencode(path.suffix, frame)
    if not ok:
        raise ValueError('无法编码输出图片。')
    encoded.tofile(path)


def select_region(frame):
    height, width = frame.shape[:2]
    scale = min(1.0, 1200 / width, 750 / height)
    preview = cv2.resize(frame, (round(width * scale), round(height * scale)))
    name = 'Select watermark - ENTER confirm / ESC cancel'
    try:
        x, y, w, h = cv2.selectROI(name, preview, showCrosshair=True)
    finally:
        cv2.destroyAllWindows()
    if not w or not h:
        raise InterruptedError('已取消。')
    left, top = int(x / scale), int(y / scale)
    right = min(width, math.ceil((x + w) / scale))
    bottom = min(height, math.ceil((y + h) / scale))
    if right - left < 4 or bottom - top < 4:
        raise ValueError('选区过小，请重新框选。')
    return left, top, right - left, bottom - top


def repair(frame, box, padding=3):
    x, y, w, h = box
    height, width = frame.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[max(0, y-padding):min(height, y+h+padding),
         max(0, x-padding):min(width, x+w+padding)] = 255
    return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)


def features(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_32F, ksize=3)


def locate(frame, template, previous, threshold=0.60):
    """Search locally first, then globally for jumps; do not update template."""
    edge = features(frame)
    height, width = edge.shape
    th, tw = template.shape
    x, y, _, _ = previous
    margin = max(80, tw, th)
    left, top = max(0, x-margin), max(0, y-margin)
    right, bottom = min(width, x+tw+margin), min(height, y+th+margin)
    result = cv2.matchTemplate(edge[top:bottom, left:right], template,
                               cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(result)
    position = (point[0]+left, point[1]+top)
    if not np.isfinite(score) or score < threshold:
        result = cv2.matchTemplate(edge, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, position = cv2.minMaxLoc(result)
    if not np.isfinite(score) or score < threshold:
        return None, float(score) if np.isfinite(score) else 0.0
    return (position[0], position[1], tw, th), float(score)


def interpolate_box(index, keyframes):
    if index <= keyframes[0][0]:
        return keyframes[0][1]
    for (start, a), (end, b) in zip(keyframes, keyframes[1:]):
        if start <= index <= end:
            weight = (index-start)/(end-start)
            return tuple(round(u+(v-u)*weight) for u, v in zip(a, b))
    return keyframes[-1][1]


def match_image(frame, reference, box, threshold=0.60):
    """Search the entire image at several scales, including resolution scaling."""
    x, y, w, h = box
    crop = reference[y:y+h, x:x+w]
    edge = features(frame)
    height, width = frame.shape[:2]
    ratios = {1.0, width/reference.shape[1], height/reference.shape[0]}
    sizes = {(max(4, round(w*r*s)), max(4, round(h*r*s)))
             for r in ratios for s in (0.75, 0.9, 1.0, 1.1, 1.25)}
    best, best_score = None, -1.0
    for tw, th in sorted(sizes):
        if tw > width or th > height:
            continue
        template = features(cv2.resize(crop, (tw, th)))
        if np.std(template) < 1e-3:
            continue
        result = cv2.matchTemplate(edge, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(result)
        if np.isfinite(score) and score > best_score:
            best_score = float(score)
            best = (*point, tw, th)
    return (best if best_score >= threshold else None), best_score


def process_images(paths, reference, box, output, progress=None, cancelled=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, item in enumerate(paths):
        if cancelled is not None and cancelled():
            break
        path = Path(item)
        record = {'source': str(path)}
        try:
            frame = read_image(path)
            current, score = (box, 1.0) if index == 0 else match_image(frame, reference, box)
            record['score'] = round(score, 3)
            if current is None:
                unmatched = output / '未匹配'
                unmatched.mkdir(exist_ok=True)
                destination = unmatched / path.name
                # Preserve the recognizable original name, disambiguating duplicates.
                suffix = 2
                while destination.exists():
                    destination = unmatched / f'{path.stem}_{suffix}{path.suffix}'
                    suffix += 1
                with path.open('rb') as source, destination.open('xb') as target:
                    shutil.copyfileobj(source, target)
                record.update(status='unmatched', output=str(destination))
            else:
                destination = output / f'{index+1:04d}_{path.stem}_cleaned.png'
                # Exclusive output creation: never replace an existing file.
                result = repair(frame, current)
                ok, encoded = cv2.imencode('.png', result)
                if not ok:
                    raise ValueError('图片编码失败')
                with destination.open('xb') as handle:
                    handle.write(encoded.tobytes())
                record.update(status='cleaned', box=list(current), output=str(destination))
        except Exception as error:
            record.update(status='error', error=str(error))
        records.append(record)
        if progress:
            progress(index+1, len(paths), path.name)
    report = output / 'batch_report.json'
    report.write_text(json.dumps({'total': len(paths), 'processed': len(records),
        'cancelled': len(records) < len(paths), 'files': records},
        ensure_ascii=False, indent=2), encoding='utf-8')
    return records, report


def batch_image_dialog(root):
    paths = filedialog.askopenfilenames(title='选择一套图片（可多选；第一张用于框选水印）',
        filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff')])
    if not paths:
        return
    reference = read_image(paths[0])
    messagebox.showinfo('框选参考水印', f'共选择 {len(paths)} 张图片。\n'
        f'请在 {Path(paths[0]).name} 上紧贴水印框选，按 Enter 确认。\n'
        '其余图片会自动寻找相似水印；未匹配的原图会复制到结果中的“未匹配”文件夹。')
    box = select_region(reference)
    x, y, w, h = box
    if np.std(features(reference)[y:y+h, x:x+w]) < 1e-3:
        raise ValueError('选区没有足够纹理，请框选包含文字或 Logo 的区域。')
    folder = filedialog.askdirectory(title='选择输出位置（自动新建结果子文件夹）')
    if not folder:
        return
    output = Path(tempfile.mkdtemp(prefix='cleaned_', dir=folder))
    window = tk.Toplevel(root)
    window.title('图片批量去水印')
    window.geometry('500x180')
    window.resizable(False, False)
    label = ttk.Label(window, text='正在准备处理…', wraplength=460)
    label.pack(padx=20, pady=20)
    bar = ttk.Progressbar(window, maximum=len(paths), length=460)
    bar.pack(padx=20)
    cancel = threading.Event()
    button = ttk.Button(window, text='取消剩余任务', command=cancel.set)
    button.pack(pady=15)
    window.protocol('WM_DELETE_WINDOW', cancel.set)
    events = queue.Queue()

    def worker():
        try:
            result = process_images(paths, reference, box, output,
                progress=lambda *args: events.put(('progress', args)),
                cancelled=cancel.is_set)
            events.put(('done', result))
        except Exception as error:
            events.put(('error', str(error)))

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    done, total, name = value
                    bar['value'] = done
                    label['text'] = f'{done}/{total}：{name}'
                else:
                    window.destroy()
                    root.deiconify()
                    if kind == 'error':
                        messagebox.showerror('处理失败', value, parent=root)
                    else:
                        records, report = value
                        cleaned = sum(r['status'] == 'cleaned' for r in records)
                        skipped = sum(r['status'] == 'unmatched' for r in records)
                        failed = sum(r['status'] == 'error' for r in records)
                        messagebox.showinfo('批量处理结束',
                            f'已修复 {cleaned} 张，未匹配 {skipped} 张，失败 {failed} 张。\n'
                            f'未处理 {len(paths)-len(records)} 张。\n'
                            f'结果文件夹：{output}\n'
                            + ('未匹配图片已放入“未匹配”子文件夹（原图保留）。\n' if skipped else '')
                            + f'详情：{report.name}', parent=root)
                    return
        except queue.Empty:
            pass
        if cancel.is_set():
            button['state'] = 'disabled'
            label['text'] = '正在完成当前图片并停止…'
        root.after(100, poll)

    threading.Thread(target=worker, daemon=True).start()
    poll()
    return window


def select_keyframes(capture, first_box, fps):
    keys = {0: first_box}
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    while True:
        seconds = simpledialog.askfloat('添加关键位置',
            '输入下一处水印位置的时间（秒），再框选该帧水印。\n'
            '水印转弯、变形或变大小时建议添加。点击取消结束标记。\n'
            '关键位置之间按直线移动，最后位置之后保持不动。', minvalue=0)
        if seconds is None:
            break
        index = round(seconds*fps)
        if frame_count > 0 and index >= frame_count:
            messagebox.showwarning('时间超出范围', '请选择视频时长内的时间。')
            continue
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            messagebox.showwarning('读取失败', '无法读取该时间的画面。')
            continue
        try:
            keys[index] = select_region(frame)
        except InterruptedError:
            continue
    capture.set(cv2.CAP_PROP_POS_FRAMES, 1)
    return sorted(keys.items())


def process_video(source, destination, first, capture, box, keyframes=None,
                  show_preview=True):
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('无法读取视频帧率。')
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    height, width = first.shape[:2]
    x, y, w, h = box
    template = features(first)[y:y+h, x:x+w].copy()
    if keyframes is None and np.std(template) < 1e-3:
        raise ValueError('选区没有足够纹理，无法跟踪；请框选文字或 Logo。')
    # A temporary directory prevents incomplete output from being delivered.
    records = []
    missed = 0
    index = 0
    last = box
    with tempfile.TemporaryDirectory(prefix='watermark_cleaner_') as folder:
        silent = Path(folder) / 'silent.mp4'
        merged = Path(folder) / 'merged.mp4'
        writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*'mp4v'),
                                 fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError('无法创建视频编码器。')
        frame = first
        try:
            while frame is not None:
                if frame.shape[:2] != (height, width):
                    raise ValueError('暂不支持中途改变尺寸的视频。')
                if keyframes is not None:
                    current, score = interpolate_box(index, keyframes), 1.0
                else:
                    current, score = (box, 1.0) if index == 0 else locate(frame, template, last)
                if current is None:
                    # Leave uncertain frames untouched, record them for review.
                    missed += 1
                    records.append({'frame': index, 'seconds': round(index/fps, 3),
                                    'score': round(score, 3)})
                    output = frame
                else:
                    last = current
                    output = repair(frame, current)
                writer.write(output)
                preview = output.copy()
                if current is not None:
                    bx, by, bw, bh = current
                    cv2.rectangle(preview, (bx, by), (bx+bw, by+bh), (0, 220, 0), 2)
                label = f'{index+1}/{count if count > 0 else "?"}  missed:{missed}  ESC:cancel'
                cv2.putText(preview, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 220, 255), 2)
                ratio = min(1.0, 1100/width, 700/height)
                if show_preview:
                    cv2.imshow('Processing preview', cv2.resize(preview,
                               (round(width*ratio), round(height*ratio))))
                if show_preview and cv2.waitKey(1) & 0xff == 27:
                    raise InterruptedError('已取消，未保存输出视频。')
                index += 1
                ok, frame = capture.read()
                if not ok:
                    frame = None
        finally:
            writer.release()
            cv2.destroyAllWindows()
        if count > 0 and index < count - 1:
            raise RuntimeError(f'视频提前停止解码（{index}/{count} 帧），未保存输出。')
        # Encode H.264 and copy source audio by transcoding it to AAC.
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y',
                   '-i', str(silent), '-i', str(source),
                   '-map', '0:v:0', '-map', '1:a?', '-c:v', 'libx264',
                   '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p',
                   '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                   '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(merged)]
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('音视频合成失败：\n' + result.stderr[-2000:])
        # Destination lives on a potentially different drive from the temp dir.
        import shutil
        shutil.copyfile(merged, destination)
    report = destination.with_suffix('.tracking.json')
    report.write_text(json.dumps({'processed_frames': index, 'fps': fps,
                                 'unrepaired_frames': records}, ensure_ascii=False,
                                indent=2), encoding='utf-8')
    return missed, report


def video_dialog(root):
    capture = None
    try:
        selected = filedialog.askopenfilename(title='选择视频', filetypes=[
            ('视频', '*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv'), ('所有文件', '*.*')])
        if not selected:
            return
        source = Path(selected)
        capture = cv2.VideoCapture(str(source))
        ok, first = capture.read()
        if not ok:
            raise ValueError('无法读取视频第一帧。')
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('无法读取视频帧率。')
        box = select_region(first)
        keyframes = None
        automatic = messagebox.askyesno('选择视频处理方式',
            '是否使用自动跟踪？\n\n是：外观固定、位置移动的水印。\n'
            '否：外观变化的水印，手动标记关键位置后自动插值处理。')
        if not automatic:
            keyframes = select_keyframes(capture, box, fps)
        extension = '.mp4'
        selected = filedialog.asksaveasfilename(title='保存修复结果',
            initialdir=str(source.parent), initialfile=source.stem+'_cleaned'+extension,
            defaultextension=extension,
            filetypes=[('MP4 视频', '*'+extension)])
        if not selected:
            return
        destination = Path(selected)
        if destination.resolve() == source.resolve():
            raise ValueError('请使用新的输出文件名，不能覆盖输入文件。')
        if destination.suffix.lower() != extension:
            raise ValueError('输出文件后缀必须为 '+extension)
        missed, report = process_video(source, destination, first, capture, box,
                                        keyframes=keyframes)
        messagebox.showinfo('完成', f'已保存：{destination}\n'
            f'{missed} 帧因匹配不可靠而保留原画面。\n跟踪报告：{report}')
    except InterruptedError:
        pass
    except Exception as error:
        messagebox.showerror('处理失败', str(error))
    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()


def create_home(root):
    root.title('图片与视频去水印')
    root.geometry('520x360')
    root.resizable(False, False)
    panel = ttk.Frame(root, padding=30)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='选择要处理的内容', font=('Microsoft YaHei UI', 19)).pack(pady=(5, 20))

    def start(mode):
        root.withdraw()
        pending = None
        try:
            if mode == 'images':
                pending = batch_image_dialog(root)
            else:
                video_dialog(root)
        except InterruptedError:
            pass
        except Exception as error:
            messagebox.showerror('处理失败', str(error), parent=root)
        finally:
            if pending is None:
                root.deiconify()

    ttk.Button(panel, text='图片 · 单张 / 批量去水印',
               command=lambda: start('images')).pack(fill='x', ipady=12, pady=8)
    ttk.Button(panel, text='视频 · 移动水印处理',
               command=lambda: start('video')).pack(fill='x', ipady=12, pady=8)
    ttk.Label(panel, text='图片：框选一次，匹配整套图片中的相似水印。\n'
        '结果另存，保留原文件。复杂背景可能留下修复痕迹。',
        wraplength=450, foreground='#666666').pack(pady=18)


def main():
    root = tk.Tk()
    create_home(root)
    root.mainloop()


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        try:
            import smoke_test
            probe_root = tk.Tk()
            probe_root.withdraw()
            create_home(probe_root)
            probe_root.update()
            probe_root.destroy()
            smoke_test.run()
            Path(sys.argv[2]).write_text('PASS: GUI runtime, image repair, tracking, '
                'keyframes, video output and audio', encoding='utf-8')
        except Exception:
            import traceback
            Path(sys.argv[2]).write_text(traceback.format_exc(), encoding='utf-8')
            raise
    else:
        main()
