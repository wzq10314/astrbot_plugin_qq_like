"""Compact QQ official menu previews; source artwork remains untouched."""
from io import BytesIO

from PIL import Image, ImageOps


def encode_menu_preview(source: bytes) -> bytes:
    with Image.open(BytesIO(source)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
            rgba = image.convert('RGBA')
            canvas = Image.new('RGB', rgba.size, 'white')
            canvas.paste(rgba, mask=rgba.getchannel('A'))
        else:
            canvas = image.convert('RGB')
        output = BytesIO()
        # Keep the approved proportions and text dimensions; only the official
        # menu preview changes format. The source and original downloads do not.
        canvas.save(output, format='WEBP', quality=90, method=5)
        return output.getvalue()
