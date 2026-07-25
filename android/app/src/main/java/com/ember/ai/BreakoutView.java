package com.ember.ai;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;

/** Breakout — drag the paddle, clear the bricks. */
public class BreakoutView extends GameView {

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Handler handler = new Handler(Looper.getMainLooper());

    private int w, h;
    private float bx, by, vx, vy, radius;
    private float paddleX, paddleY, paddleW, paddleH;
    private boolean[][] bricks;
    private int brickRows = 5, brickCols = 6;
    private float brickTop, brickH, brickGap;
    private int remaining, score, lives;
    private boolean running, started;

    private final int[] BRICK_COLORS = {
            Color.parseColor("#E0451B"), Color.parseColor("#FF6A1A"),
            Color.parseColor("#FF9330"), Color.parseColor("#FFC24B"),
            Color.parseColor("#F7E3C4")
    };

    private final Runnable loop = new Runnable() {
        public void run() {
            if (!running) return;
            update();
            invalidate();
            handler.postDelayed(this, 16);
        }
    };

    public BreakoutView(Context c, Host host) {
        super(c, host);
    }

    @Override
    protected void onSizeChanged(int nw, int nh, int ow, int oh) {
        w = nw;
        h = nh;
        radius = dp(8);
        paddleW = dp(90);
        paddleH = dp(14);
        paddleY = h - dp(48);
        brickGap = dp(6);
        brickTop = dp(60);
        brickH = dp(26);
        if (!started) {
            started = true;
            restart();
        }
    }

    @Override
    public void restart() {
        handler.removeCallbacks(loop);
        paddleX = w / 2f;
        bricks = new boolean[brickRows][brickCols];
        remaining = brickRows * brickCols;
        for (int r = 0; r < brickRows; r++)
            for (int c = 0; c < brickCols; c++) bricks[r][c] = true;
        score = 0;
        lives = 3;
        resetBall();
        running = true;
        score("Score 0  ·  Lives 3");
        status("Drag to move the paddle");
        handler.postDelayed(loop, 300);
        invalidate();
    }

    @Override
    public void stop() {
        running = false;
        handler.removeCallbacks(loop);
    }

    private void resetBall() {
        bx = w / 2f;
        by = paddleY - dp(30);
        vx = dp(3.2f) * (Math.random() > 0.5 ? 1 : -1);
        vy = -dp(4.2f);
    }

    private void update() {
        bx += vx;
        by += vy;
        if (bx - radius < 0) {
            bx = radius;
            vx = -vx;
        } else if (bx + radius > w) {
            bx = w - radius;
            vx = -vx;
        }
        if (by - radius < 0) {
            by = radius;
            vy = -vy;
        }
        // Paddle
        RectF paddle = new RectF(paddleX - paddleW / 2, paddleY, paddleX + paddleW / 2, paddleY + paddleH);
        if (vy > 0 && by + radius >= paddle.top && by + radius <= paddle.bottom + Math.abs(vy)
                && bx >= paddle.left - radius && bx <= paddle.right + radius) {
            vy = -Math.abs(vy);
            by = paddle.top - radius;
            float hit = (bx - paddleX) / (paddleW / 2); // -1..1
            vx = dp(4.5f) * Math.max(-1, Math.min(1, hit));
        }
        // Bricks
        if (bricks != null) {
            for (int r = 0; r < brickRows; r++) {
                for (int col = 0; col < brickCols; col++) {
                    if (!bricks[r][col]) continue;
                    RectF br = brickRect(r, col);
                    if (bx + radius > br.left && bx - radius < br.right
                            && by + radius > br.top && by - radius < br.bottom) {
                        bricks[r][col] = false;
                        remaining--;
                        score++;
                        vy = -vy;
                        score("Score " + score + "  ·  Lives " + lives);
                        if (remaining == 0) {
                            running = false;
                            status("You cleared the board! Score " + score + ". Tap Restart.");
                        }
                        return; // one brick per frame keeps physics clean
                    }
                }
            }
        }
        // Fell below paddle
        if (by - radius > h) {
            lives--;
            if (lives <= 0) {
                running = false;
                status("Game over — score " + score + ". Tap Restart.");
            } else {
                score("Score " + score + "  ·  Lives " + lives);
                resetBall();
            }
        }
    }

    private RectF brickRect(int r, int col) {
        float totalGap = brickGap * (brickCols + 1);
        float bw = (w - totalGap) / brickCols;
        float left = brickGap + col * (bw + brickGap);
        float top = brickTop + r * (brickH + brickGap);
        return new RectF(left, top, left + bw, top + brickH);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        float x = e.getX();
        paddleX = Math.max(paddleW / 2, Math.min(w - paddleW / 2, x));
        if (!running && e.getActionMasked() == MotionEvent.ACTION_DOWN) {
            // tapping does nothing here; Restart button handles restart
        }
        invalidate();
        return true;
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawColor(Color.parseColor("#0E0B0A"));
        if (bricks != null) {
            for (int r = 0; r < brickRows; r++) {
                for (int col = 0; col < brickCols; col++) {
                    if (!bricks[r][col]) continue;
                    paint.setColor(BRICK_COLORS[r % BRICK_COLORS.length]);
                    RectF br = brickRect(r, col);
                    c.drawRoundRect(br, dp(4), dp(4), paint);
                }
            }
        }
        paint.setColor(Color.parseColor("#F5EFEA"));
        c.drawRoundRect(new RectF(paddleX - paddleW / 2, paddleY,
                paddleX + paddleW / 2, paddleY + paddleH), dp(7), dp(7), paint);
        paint.setColor(Color.parseColor("#FFC24B"));
        c.drawCircle(bx, by, radius, paint);
    }
}
