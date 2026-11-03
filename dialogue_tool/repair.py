from pathlib import Path

from PIL import Image, ImageDraw


def rectangle_mask(size: tuple[int, int], rect: tuple[int, int, int, int]) -> Image.Image:
    x1, y1, x2, y2 = rect
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError('水印矩形必须完全位于图片内部，格式为左上x,y,右下x,y')
    mask = Image.new('L', size, 0)
    ImageDraw.Draw(mask).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
    return mask


def composite_repair(source: Image.Image, generated: Image.Image, mask: Image.Image) -> Image.Image:
    if source.size != mask.size or source.size != generated.size:
        raise ValueError('原图、修复图及遮罩尺寸必须相同')
    # 模型可能改变遮罩外像素。只合成遮罩内的修复结果，人物及其他区域保持原样。
    return Image.composite(generated.convert('RGB'), source.convert('RGB'), mask.convert('L'))


def repair_image(source: Path, mask_path: Path, output: Path, model_path: Path) -> dict:
    import time
    if not model_path.is_file():
        raise ValueError('缺少本地 LaMa 模型；可跳过修图，继续拆组和即梦生成')
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise ValueError('修图需要额外安装 CPU PyTorch；无独显设备的主流程不需要 PyTorch') from exc

    if output.exists() or source.resolve() == output.resolve():
        raise ValueError('修复图不能覆盖输入或已有文件')
    if not model_path.is_file():
        raise ValueError('缺少本地 LaMa 模型，请先准备模型文件')
    with Image.open(source) as im, Image.open(mask_path) as m:
        image, mask = im.convert('RGB'), m.convert('L')
    if image.size != mask.size or mask.getbbox() is None:
        raise ValueError('遮罩尺寸不符或为空')
    start = time.monotonic()
    a = np.asarray(image).astype(np.float32).transpose(2, 0, 1) / 255.0
    b = (np.asarray(mask) > 0).astype(np.float32)[None, :, :]
    h, w = a.shape[1:]
    # LaMa 的空间尺寸要求为 8 的倍数；裁回原始尺寸后才合成。
    padding = ((0, 0), (0, (-h) % 8), (0, (-w) % 8))
    a = np.pad(a, padding, mode='symmetric')
    b = np.pad(b, padding, mode='symmetric')
    torch.set_num_threads(min(4, torch.get_num_threads()))
    model = torch.jit.load(str(model_path), map_location='cpu').eval()
    with torch.inference_mode():
        prediction = model(torch.from_numpy(a)[None], torch.from_numpy(b)[None])
    array = prediction[0, :, :h, :w].permute(1, 2, 0).cpu().numpy()
    generated = Image.fromarray(np.clip(array * 255.0, 0, 255).astype(np.uint8))
    output.parent.mkdir(parents=True, exist_ok=True)
    composite_repair(image, generated, mask).save(output)
    return {'source': str(source), 'mask': str(mask_path), 'output': str(output),
            'device': 'cpu', 'elapsed_seconds': round(time.monotonic() - start, 3)}
