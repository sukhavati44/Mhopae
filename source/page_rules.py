"""Displayed-page normalized coordinates; no PDF source objects are retained."""
import copy
import math
from dataclasses import dataclass, field


@dataclass
class PageRules:
    cut: float = .18
    masks: list = field(default_factory=list)
    cuts: dict = field(default_factory=dict)
    page_masks: dict = field(default_factory=dict)

    def for_page(self, page):
        return self.cuts.get(page, self.cut), copy.deepcopy(self.page_masks.get(page, self.masks))

    def snapshot(self):
        return copy.deepcopy(self)

    def token(self):
        return (self.cut, tuple(map(tuple,self.masks)), tuple(sorted(self.cuts.items())),
                tuple((k,tuple(map(tuple,v))) for k,v in sorted(self.page_masks.items())))

    def exceptions(self):
        return sorted(set(self.cuts)|set(self.page_masks))


def rectangle(values):
    if len(values)!=4 or any(not math.isfinite(v) for v in values):
        raise ValueError('마스킹 좌표는 유한한 네 값이어야 합니다.')
    x0,y0,x1,y1=values
    if not (0<=x0<x1<=1 and 0<=y0<y1<=1):
        raise ValueError('마스킹은 페이지 내부의 양의 크기 사각형이어야 합니다.')
    return (x0,y0,x1,y1)


def white_pixels(pix, page_rect, masks, dpi):
    """Outward rounding covers boundary pixels. pix origin includes clipped top."""
    from PIL import Image, ImageDraw
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    draw=ImageDraw.Draw(im)
    for values in masks:
        x0,y0,x1,y1=rectangle(values)
        left=math.floor((page_rect.x0+x0*page_rect.width)*dpi/72)-pix.x
        top=math.floor((page_rect.y0+y0*page_rect.height)*dpi/72)-pix.y
        right=math.ceil((page_rect.x0+x1*page_rect.width)*dpi/72)-pix.x
        bottom=math.ceil((page_rect.y0+y1*page_rect.height)*dpi/72)-pix.y
        left,top=max(0,left),max(0,top)
        right,bottom=min(pix.width,right),min(pix.height,bottom)
        if right>left and bottom>top:draw.rectangle((left,top,right-1,bottom-1),fill=(255,255,255))
    return im
