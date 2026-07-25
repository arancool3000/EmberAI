package com.ember.ai;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Memory Match — flip cards, find the pairs. */
public class MemoryView extends GameView {

    private static final int COLS = 4, ROWS = 4;
    private static final String[] FACES = {"🔥", "⭐", "🌙", "⚡", "🎮", "🍕", "🚀", "🎧"};

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Handler handler = new Handler(Looper.getMainLooper());

    private int[] value = new int[COLS * ROWS];
    private boolean[] faceUp = new boolean[COLS * ROWS];
    private boolean[] matched = new boolean[COLS * ROWS];
    private int first = -1, second = -1, moves, pairs;
    private boolean busy;
    private int cell, offX, offY;

    public MemoryView(Context c, Host host) {
        super(c, host);
        text.setTextAlign(Paint.Align.CENTER);
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        int side = Math.min(w, h) - (int) dp(24);
        cell = side / COLS;
        offX = (w - cell * COLS) / 2;
        offY = (h - cell * ROWS) / 2;
        text.setTextSize(cell * 0.42f);
    }

    @Override
    public void restart() {
        List<Integer> deck = new ArrayList<Integer>();
        for (int i = 0; i < FACES.length; i++) {
            deck.add(i);
            deck.add(i);
        }
        Collections.shuffle(deck);
        for (int i = 0; i < value.length; i++) {
            value[i] = deck.get(i);
            faceUp[i] = false;
            matched[i] = false;
        }
        first = second = -1;
        moves = 0;
        pairs = 0;
        busy = false;
        score("Moves 0");
        status("Find all 8 pairs");
        invalidate();
    }

    @Override
    public void stop() {
        handler.removeCallbacksAndMessages(null);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        if (e.getActionMasked() != MotionEvent.ACTION_DOWN) return true;
        if (busy) return true;
        int col = (int) ((e.getX() - offX) / cell);
        int row = (int) ((e.getY() - offY) / cell);
        if (col < 0 || col >= COLS || row < 0 || row >= ROWS) return true;
        int idx = row * COLS + col;
        if (matched[idx] || faceUp[idx]) return true;

        faceUp[idx] = true;
        if (first == -1) {
            first = idx;
        } else {
            second = idx;
            moves++;
            score("Moves " + moves);
            busy = true;
            if (value[first] == value[second]) {
                handler.postDelayed(new Runnable() {
                    public void run() {
                        matched[first] = true;
                        matched[second] = true;
                        first = second = -1;
                        busy = false;
                        pairs++;
                        if (pairs == FACES.length) {
                            status("Solved in " + moves + " moves! Tap Restart.");
                        }
                        invalidate();
                    }
                }, 250);
            } else {
                handler.postDelayed(new Runnable() {
                    public void run() {
                        faceUp[first] = false;
                        faceUp[second] = false;
                        first = second = -1;
                        busy = false;
                        invalidate();
                    }
                }, 700);
            }
        }
        invalidate();
        return true;
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawColor(Color.parseColor("#0E0B0A"));
        float pad = dp(5);
        for (int i = 0; i < value.length; i++) {
            int col = i % COLS, row = i / COLS;
            float left = offX + col * cell + pad;
            float top = offY + row * cell + pad;
            RectF r = new RectF(left, top, left + cell - 2 * pad, top + cell - 2 * pad);
            boolean show = faceUp[i] || matched[i];
            paint.setColor(matched[i] ? Color.parseColor("#2A1D12")
                    : show ? Color.parseColor("#241C17") : Color.parseColor("#E0451B"));
            c.drawRoundRect(r, dp(12), dp(12), paint);
            if (show) {
                float cx = r.centerX();
                float cy = r.centerY() - (text.ascent() + text.descent()) / 2f;
                c.drawText(FACES[value[i]], cx, cy, text);
            }
        }
    }
}
