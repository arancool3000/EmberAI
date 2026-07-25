package com.ember.ai;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.MotionEvent;

import java.util.Random;

/** 2048 — swipe to slide and merge tiles. */
public class Game2048View extends GameView {

    private static final int N = 4;
    private final int[][] board = new int[N][N];
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Random rnd = new Random();
    private int score;
    private boolean over;
    private float downX, downY;
    private int size, offX, offY, gap;

    public Game2048View(Context c, Host host) {
        super(c, host);
        text.setColor(Color.parseColor("#1A1512"));
        text.setFakeBoldText(true);
        text.setTextAlign(Paint.Align.CENTER);
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        int side = Math.min(w, h) - (int) dp(24);
        size = side / N;
        gap = (int) dp(6);
        offX = (w - side) / 2;
        offY = (h - side) / 2;
        text.setTextSize(size * 0.34f);
    }

    @Override
    public void restart() {
        for (int r = 0; r < N; r++)
            for (int col = 0; col < N; col++) board[r][col] = 0;
        score = 0;
        over = false;
        addTile();
        addTile();
        score("Score 0");
        status("Reach 2048!");
        invalidate();
    }

    private void addTile() {
        int empties = 0;
        for (int r = 0; r < N; r++)
            for (int c = 0; c < N; c++) if (board[r][c] == 0) empties++;
        if (empties == 0) return;
        int target = rnd.nextInt(empties);
        int seen = 0;
        int val = rnd.nextInt(10) == 0 ? 4 : 2;
        for (int r = 0; r < N; r++) {
            for (int c = 0; c < N; c++) {
                if (board[r][c] == 0) {
                    if (seen == target) {
                        board[r][c] = val;
                        return;
                    }
                    seen++;
                }
            }
        }
    }

    // Compress+merge one line toward index 0. Mutates and returns whether it changed.
    private boolean collapse(int[] line) {
        int[] before = new int[]{line[0], line[1], line[2], line[3]};
        int[] tmp = new int[N];
        int idx = 0;
        for (int i = 0; i < N; i++) if (line[i] != 0) tmp[idx++] = line[i];
        for (int i = 0; i < N; i++) line[i] = tmp[i];
        for (int i = 0; i < N - 1; i++) {
            if (line[i] != 0 && line[i] == line[i + 1]) {
                line[i] *= 2;
                score += line[i];
                for (int j = i + 1; j < N - 1; j++) line[j] = line[j + 1];
                line[N - 1] = 0;
            }
        }
        for (int i = 0; i < N; i++) if (line[i] != before[i]) return true;
        return false;
    }

    private boolean move(int dir) { // 0 left, 1 right, 2 up, 3 down
        boolean moved = false;
        for (int i = 0; i < N; i++) {
            int[] line = new int[N];
            for (int j = 0; j < N; j++) line[j] = read(dir, i, j);
            if (collapse(line)) {
                moved = true;
                for (int j = 0; j < N; j++) write(dir, i, j, line[j]);
            }
        }
        return moved;
    }

    // Map a (line i, position j-from-front) to board cell, per direction.
    private int read(int dir, int i, int j) {
        switch (dir) {
            case 0: return board[i][j];
            case 1: return board[i][N - 1 - j];
            case 2: return board[j][i];
            default: return board[N - 1 - j][i];
        }
    }

    private void write(int dir, int i, int j, int v) {
        switch (dir) {
            case 0: board[i][j] = v; break;
            case 1: board[i][N - 1 - j] = v; break;
            case 2: board[j][i] = v; break;
            default: board[N - 1 - j][i] = v; break;
        }
    }

    private boolean canMove() {
        for (int r = 0; r < N; r++)
            for (int c = 0; c < N; c++) {
                if (board[r][c] == 0) return true;
                if (c + 1 < N && board[r][c] == board[r][c + 1]) return true;
                if (r + 1 < N && board[r][c] == board[r + 1][c]) return true;
            }
        return false;
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = e.getX();
                downY = e.getY();
                return true;
            case MotionEvent.ACTION_UP:
                if (over) return true;
                float dx = e.getX() - downX, dy = e.getY() - downY;
                if (Math.abs(dx) < dp(18) && Math.abs(dy) < dp(18)) return true;
                int dir;
                if (Math.abs(dx) > Math.abs(dy)) dir = dx > 0 ? 1 : 0;
                else dir = dy > 0 ? 3 : 2;
                if (move(dir)) {
                    addTile();
                    score("Score " + score);
                    if (!canMove()) {
                        over = true;
                        status("No moves left — score " + score + ". Tap Restart.");
                    }
                    invalidate();
                }
                return true;
        }
        return true;
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawColor(Color.parseColor("#0E0B0A"));
        paint.setColor(Color.parseColor("#160F0B"));
        c.drawRoundRect(new RectF(offX - gap, offY - gap,
                offX + N * size + gap, offY + N * size + gap), dp(10), dp(10), paint);
        for (int r = 0; r < N; r++) {
            for (int col = 0; col < N; col++) {
                float left = offX + col * size + gap / 2f;
                float top = offY + r * size + gap / 2f;
                RectF rect = new RectF(left, top, left + size - gap, top + size - gap);
                int v = board[r][col];
                paint.setColor(tileColor(v));
                c.drawRoundRect(rect, dp(8), dp(8), paint);
                if (v != 0) {
                    text.setColor(v <= 4 ? Color.parseColor("#3A2A12") : Color.parseColor("#160F0B"));
                    float cx = rect.centerX();
                    float cy = rect.centerY() - (text.ascent() + text.descent()) / 2f;
                    c.drawText(String.valueOf(v), cx, cy, text);
                }
            }
        }
    }

    private int tileColor(int v) {
        switch (v) {
            case 0: return Color.parseColor("#241C17");
            case 2: return Color.parseColor("#F7E3C4");
            case 4: return Color.parseColor("#F6D9A0");
            case 8: return Color.parseColor("#FFC24B");
            case 16: return Color.parseColor("#FFA83A");
            case 32: return Color.parseColor("#FF9330");
            case 64: return Color.parseColor("#FF7A22");
            case 128: return Color.parseColor("#FF6A1A");
            case 256: return Color.parseColor("#F5561C");
            case 512: return Color.parseColor("#E0451B");
            case 1024: return Color.parseColor("#D23A18");
            default: return Color.parseColor("#C0301A");
        }
    }
}
