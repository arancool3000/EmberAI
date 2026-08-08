"""Prove the drawn pointer lands where a tap will land.

The mirror's click mapping turns a tap at fraction (fx,fy) of the image into a
logical point (fx*lw, fy*lh). The cursor must therefore be drawn at exactly the
inverse of that, or the pointer would show one place and click another - which
is worse than no pointer at all.
"""
import io, sys, types

# Stand in for pyautogui: the module is not installed here and would need a
# display anyway. Only .position() is used by the code under test.
POS = [0, 0]
fake = types.ModuleType('pyautogui')
fake.position = lambda: (POS[0], POS[1])
fake.size = lambda: (1440, 900)
sys.modules['pyautogui'] = fake

src = io.open(__import__('os').path.join(__import__('os').path.dirname(__file__),'remote_server.py'), encoding='utf-8').read()
ns = {}
start = src.index('# Classic arrow, drawn at the origin')
end = src.index('def _lan_ip()')
exec(compile(src[start:end], 'remote_server_extract', 'exec'), ns)
_draw_cursor = ns['_draw_cursor']

from PIL import Image

passed = failed = 0
def ok(name, cond, extra=''):
    global passed, failed
    if cond: passed += 1
    else: failed += 1
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -- ' + str(extra)))

def darkest_point(img):
    """Where the black arrow actually got painted."""
    px = img.load()
    best, bx, by = 255, -1, -1
    for y in range(img.height):
        for x in range(img.width):
            v = sum(px[x, y]) // 3
            if v < best:
                best, bx, by = v, x, y
    return bx, by, best

LW, LH = 1440, 900            # logical screen
IW, IH = 720, 450             # frame after downscaling for the wire

# The mapping the client uses, from remote_server's own tap handler.
def tap_to_logical(fx, fy):
    return fx * LW, fy * LH

for (cx, cy) in [(0, 0), (720, 450), (1400, 880), (360, 225)]:
    img = Image.new('RGB', (IW, IH), (200, 200, 200))
    POS[0], POS[1] = cx, cy
    _draw_cursor(img, LW, LH)
    bx, by, v = darkest_point(img)
    ok('the pointer is actually painted at logical (%d,%d)' % (cx, cy), v < 80, 'darkest=%d' % v)
    # The arrow's tip is its top-left point, so the darkest pixel sits at or
    # just below/right of the true position. Allow the arrow's own body.
    fx, fy = bx / float(IW), by / float(IH)
    lx, ly = tap_to_logical(fx, fy)
    ok('...and a tap there maps back to within the arrow (%d,%d)' % (cx, cy),
       abs(lx - cx) < 90 and abs(ly - cy) < 90, 'drew at logical (%.0f,%.0f)' % (lx, ly))

# It must survive the things that actually go wrong.
img = Image.new('RGB', (IW, IH), (255, 255, 255))
POS[0], POS[1] = 5000, 5000
before = list(img.getdata())
_draw_cursor(img, LW, LH)
ok('a pointer off the captured screen draws nothing', list(img.getdata()) == before)

img = Image.new('RGB', (IW, IH), (255, 255, 255))
before = list(img.getdata())
_draw_cursor(img, 0, 0)
ok('a zero screen size is ignored rather than dividing by zero', list(img.getdata()) == before)

def boom():
    raise RuntimeError('no display')
fake.position = boom
img = Image.new('RGB', (IW, IH), (255, 255, 255))
before = list(img.getdata())
_draw_cursor(img, LW, LH)
ok('IF THE POINTER CANNOT BE READ THE FRAME IS STILL FINE — it costs the cursor, not the mirror',
   list(img.getdata()) == before)
fake.position = lambda: (POS[0], POS[1])

# Legible on both a light and a dark window: the arrow carries its own halo.
for bg, label in [((255, 255, 255), 'a white window'), ((10, 10, 10), 'a dark window')]:
    img = Image.new('RGB', (IW, IH), bg)
    POS[0], POS[1] = 700, 400
    _draw_cursor(img, LW, LH)
    vals = [sum(p) // 3 for p in img.getdata()]
    ok('the pointer is visible on ' + label,
       (max(vals) - min(vals)) > 120, 'contrast range %d' % (max(vals) - min(vals)))

print('\n%d passed, %d failed' % (passed, failed))
sys.exit(1 if failed else 0)
