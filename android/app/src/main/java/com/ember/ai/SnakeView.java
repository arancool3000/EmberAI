package com.ember.ai;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Iterator;
import java.util.Random;

/** Classic Snake. Swipe to steer. */
public class SnakeView extends GameView {

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Random rnd = new Random();
    private final Handler handler = new Handler(Looper.getMainLooper());

    private int cols, rows, cell, offX, offY;
    private final Deque<int[]> snake = new ArrayDeque<int[]>();
    private int dirX = 1, dirY = 0, nextDirX = 1, nextDirY = 0;
    private int foodX, foodY, score;
    private boolean alive = true, started = false;
    private float downX, downY;

    private final Runnable tick = new Runnable() {
        public void run() {
            step();
            if (alive) handler.postDelayed(this, Math.max(70, 200 - score * 4));
        }
    };

    public SnakeView(Context c, Host host) {
        super(c, host);
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        cell = (int) dp(16);
        cols = Math.max(8, w / cell);
        rows = Math.max(8, h / cell);
        offX = (w - cols * cell) / 2;
        offY = (h - rows * cell) / 2;
        if (!started) {
            started = true;
            restart();
        }
    }

    @Override
    public void restart() {
        handler.removeCallbacks(tick);
        snake.clear();
        int cx = cols / 2, cy = rows / 2;
        snake.addFirst(new int[]{cx, cy});
        snake.addLast(new int[]{cx - 1, cy});
        snake.addLast(new int[]{cx - 2, cy});
        dirX = 1;
        dirY = 0;
        nextDirX = 1;
        nextDirY = 0;
        score = 0;
        alive = true;
        placeFood();
        score("Score 0");
        status("Swipe to steer");
        handler.postDelayed(tick, 250);
        invalidate();
    }

    @Override
    public void stop() {
        handler.removeCallbacks(tick);
    }

    private void placeFood() {
        while (true) {
            foodX = rnd.nextInt(cols);
            foodY = rnd.nextInt(rows);
            boolean onSnake = false;
            for (int[] s : snake) {
                if (s[0] == foodX && s[1] == foodY) {
                    onSnake = true;
                    break;
                }
            }
            if (!onSnake) return;
        }
    }

    private void step() {
        if (!alive) return;
        dirX = nextDirX;
        dirY = nextDirY;
        int[] head = snake.peekFirst();
        int nx = head[0] + dirX, ny = head[1] + dirY;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) {
            gameOver();
            return;
        }
        // Self collision (ignore the tail cell that will move away, unless we grow).
        boolean grow = (nx == foodX && ny == foodY);
        Iterator<int[]> it = snake.iterator();
        int[] tail = snake.peekLast();
        while (it.hasNext()) {
            int[] s = it.next();
            if (s == tail && !grow) continue;
            if (s[0] == nx && s[1] == ny) {
                gameOver();
                return;
            }
        }
        snake.addFirst(new int[]{nx, ny});
        if (grow) {
            score++;
            score("Score " + score);
            placeFood();
        } else {
            snake.removeLast();
        }
        invalidate();
    }

    private void gameOver() {
        alive = false;
        handler.removeCallbacks(tick);
        status("Game over — score " + score + ". Tap Restart.");
        invalidate();
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = e.getX();
                downY = e.getY();
                return true;
            case MotionEvent.ACTION_UP:
                float dx = e.getX() - downX, dy = e.getY() - downY;
                if (Math.abs(dx) < dp(12) && Math.abs(dy) < dp(12)) return true;
                if (Math.abs(dx) > Math.abs(dy)) {
                    setDir(dx > 0 ? 1 : -1, 0);
                } else {
                    setDir(0, dy > 0 ? 1 : -1);
                }
                return true;
        }
        return true;
    }

    private void setDir(int x, int y) {
        // Disallow reversing directly onto yourself.
        if (x == -dirX && y == -dirY) return;
        nextDirX = x;
        nextDirY = y;
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawColor(Color.parseColor("#0E0B0A"));
        // board
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.parseColor("#160F0B"));
        c.drawRect(offX, offY, offX + cols * cell, offY + rows * cell, paint);
        // food
        paint.setColor(Color.parseColor("#FFC24B"));
        drawCell(c, foodX, foodY, 0.18f);
        // snake
        boolean head = true;
        for (int[] s : snake) {
            paint.setColor(head ? Color.parseColor("#FF9330") : Color.parseColor("#E0451B"));
            drawCell(c, s[0], s[1], 0.12f);
            head = false;
        }
    }

    private void drawCell(Canvas c, int gx, int gy, float inset) {
        float pad = cell * inset;
        float left = offX + gx * cell + pad;
        float top = offY + gy * cell + pad;
        RectF r = new RectF(left, top, left + cell - 2 * pad, top + cell - 2 * pad);
        c.drawRoundRect(r, cell * 0.2f, cell * 0.2f, paint);
    }
}
