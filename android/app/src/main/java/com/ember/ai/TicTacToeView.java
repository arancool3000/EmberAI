package com.ember.ai;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.os.Handler;
import android.os.Looper;
import android.view.MotionEvent;

/** Tic-Tac-Toe against an unbeatable minimax AI. You are X. */
public class TicTacToeView extends GameView {

    private static final int EMPTY = 0, X = 1, O = 2; // player X, AI O
    private final int[] board = new int[9];
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean gameEnded, aiThinking;
    private int wins, losses, draws;
    private int cell, offX, offY, boardSize;

    public TicTacToeView(Context c, Host host) {
        super(c, host);
        paint.setStrokeCap(Paint.Cap.ROUND);
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        boardSize = Math.min(w, h) - (int) dp(32);
        cell = boardSize / 3;
        offX = (w - cell * 3) / 2;
        offY = (h - cell * 3) / 2;
    }

    @Override
    public void restart() {
        for (int i = 0; i < 9; i++) board[i] = EMPTY;
        gameEnded = false;
        aiThinking = false;
        score("You " + wins + "  ·  AI " + losses + "  ·  Draw " + draws);
        status("Your move — you are X");
        invalidate();
    }

    @Override
    public void stop() {
        handler.removeCallbacksAndMessages(null);
    }

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        if (e.getActionMasked() != MotionEvent.ACTION_DOWN) return true;
        if (gameEnded || aiThinking) return true;
        int col = (int) ((e.getX() - offX) / cell);
        int row = (int) ((e.getY() - offY) / cell);
        if (col < 0 || col >= 3 || row < 0 || row >= 3) return true;
        int idx = row * 3 + col;
        if (board[idx] != EMPTY) return true;

        board[idx] = X;
        invalidate();
        if (finishIfOver()) return true;

        aiThinking = true;
        status("Ember is thinking…");
        handler.postDelayed(new Runnable() {
            public void run() {
                int mv = bestMove();
                if (mv >= 0) board[mv] = O;
                aiThinking = false;
                invalidate();
                if (!finishIfOver()) status("Your move — you are X");
            }
        }, 350);
        return true;
    }

    private boolean finishIfOver() {
        int w = winner(board);
        if (w == X) {
            gameEnded = true;
            wins++;
            status("You win! Tap Restart.");
        } else if (w == O) {
            gameEnded = true;
            losses++;
            status("Ember wins. Tap Restart.");
        } else if (isFull(board)) {
            gameEnded = true;
            draws++;
            status("Draw. Tap Restart.");
        } else {
            return false;
        }
        score("You " + wins + "  ·  AI " + losses + "  ·  Draw " + draws);
        return true;
    }

    // ---- minimax ----
    private int bestMove() {
        int bestScore = Integer.MIN_VALUE, move = -1;
        for (int i = 0; i < 9; i++) {
            if (board[i] == EMPTY) {
                board[i] = O;
                int s = minimax(board, false, 0);
                board[i] = EMPTY;
                if (s > bestScore) {
                    bestScore = s;
                    move = i;
                }
            }
        }
        return move;
    }

    private int minimax(int[] b, boolean maximizing, int depth) {
        int w = winner(b);
        if (w == O) return 10 - depth;
        if (w == X) return depth - 10;
        if (isFull(b)) return 0;
        if (maximizing) {
            int best = Integer.MIN_VALUE;
            for (int i = 0; i < 9; i++) {
                if (b[i] == EMPTY) {
                    b[i] = O;
                    best = Math.max(best, minimax(b, false, depth + 1));
                    b[i] = EMPTY;
                }
            }
            return best;
        } else {
            int best = Integer.MAX_VALUE;
            for (int i = 0; i < 9; i++) {
                if (b[i] == EMPTY) {
                    b[i] = X;
                    best = Math.min(best, minimax(b, true, depth + 1));
                    b[i] = EMPTY;
                }
            }
            return best;
        }
    }

    private static final int[][] LINES = {
            {0, 1, 2}, {3, 4, 5}, {6, 7, 8}, {0, 3, 6}, {1, 4, 7}, {2, 5, 8}, {0, 4, 8}, {2, 4, 6}
    };

    private int winner(int[] b) {
        for (int[] L : LINES) {
            if (b[L[0]] != EMPTY && b[L[0]] == b[L[1]] && b[L[1]] == b[L[2]]) return b[L[0]];
        }
        return EMPTY;
    }

    private boolean isFull(int[] b) {
        for (int v : b) if (v == EMPTY) return false;
        return true;
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawColor(Color.parseColor("#0E0B0A"));
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(dp(3));
        paint.setColor(Color.parseColor("#2E241D"));
        for (int i = 1; i < 3; i++) {
            c.drawLine(offX + i * cell, offY, offX + i * cell, offY + 3 * cell, paint);
            c.drawLine(offX, offY + i * cell, offX + 3 * cell, offY + i * cell, paint);
        }
        for (int i = 0; i < 9; i++) {
            int col = i % 3, row = i / 3;
            float cx = offX + col * cell + cell / 2f;
            float cy = offY + row * cell + cell / 2f;
            float r = cell * 0.28f;
            if (board[i] == X) {
                paint.setColor(Color.parseColor("#FFC24B"));
                paint.setStrokeWidth(dp(6));
                c.drawLine(cx - r, cy - r, cx + r, cy + r, paint);
                c.drawLine(cx + r, cy - r, cx - r, cy + r, paint);
            } else if (board[i] == O) {
                paint.setColor(Color.parseColor("#FF6A1A"));
                paint.setStrokeWidth(dp(6));
                c.drawCircle(cx, cy, r, paint);
            }
        }
    }
}
