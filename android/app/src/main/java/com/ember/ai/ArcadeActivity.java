package com.ember.ai;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Ember Arcade — a small collection of games. Framework-only, built in code. */
public class ArcadeActivity extends Activity implements GameView.Host {

    private static final String[] NAMES = {"Snake", "2048", "Breakout", "Memory Match", "Tic-Tac-Toe"};
    private static final String[] ICONS = {"🐍", "🔢", "🧱", "🧠", "⭕"};
    private static final String[] DESC = {
            "Swipe to steer, eat, and grow.",
            "Slide tiles, merge to 2048.",
            "Drag the paddle, clear the bricks.",
            "Flip cards, find all the pairs.",
            "Beat the unbeatable AI — if you can."
    };

    private FrameLayout content;
    private TextView title, scoreView, statusView;
    private LinearLayout gameBar;
    private GameView game;
    private float density;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        density = getResources().getDisplayMetrics().density;

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#0E0B0A"));
        root.setFitsSystemWindows(true);

        // Header
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(px(12), px(12), px(12), px(12));
        Button back = new Button(this);
        back.setText("‹");
        back.setTextSize(22);
        back.setTextColor(Color.parseColor("#FF9330"));
        back.setBackgroundColor(Color.TRANSPARENT);
        back.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                onBackPressed();
            }
        });
        header.addView(back);
        title = new TextView(this);
        title.setText("Ember Arcade");
        title.setTextColor(Color.parseColor("#FF9330"));
        title.setTextSize(20);
        title.setPadding(px(6), 0, 0, 0);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        title.setLayoutParams(tp);
        header.addView(title);
        root.addView(header);

        View divider = new View(this);
        divider.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, px(1) / 2)));
        divider.setBackgroundColor(Color.parseColor("#2E241D"));
        root.addView(divider);

        content = new FrameLayout(this);
        content.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(content);

        // Game bar (hidden until a game is open)
        gameBar = new LinearLayout(this);
        gameBar.setOrientation(LinearLayout.HORIZONTAL);
        gameBar.setGravity(Gravity.CENTER_VERTICAL);
        gameBar.setPadding(px(16), px(8), px(16), px(12));
        gameBar.setVisibility(View.GONE);
        statusView = new TextView(this);
        statusView.setTextColor(Color.parseColor("#B9ADA3"));
        statusView.setTextSize(13);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        statusView.setLayoutParams(sp);
        gameBar.addView(statusView);
        Button restart = new Button(this);
        restart.setText("Restart");
        restart.setTextColor(Color.WHITE);
        restart.setBackgroundColor(Color.parseColor("#E0451B"));
        restart.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                if (game != null) game.restart();
            }
        });
        gameBar.addView(restart);
        root.addView(gameBar);

        setContentView(root);
        showLauncher();
    }

    private void showLauncher() {
        stopGame();
        title.setText("Ember Arcade");
        gameBar.setVisibility(View.GONE);
        content.removeAllViews();

        ScrollView sv = new ScrollView(this);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(px(16), px(12), px(16), px(24));
        for (int i = 0; i < NAMES.length; i++) {
            col.addView(makeCard(i));
        }
        sv.addView(col);
        content.addView(sv);
    }

    private View makeCard(final int index) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setPadding(px(16), px(16), px(16), px(16));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = px(10);
        card.setLayoutParams(lp);
        card.setBackground(getResources().getDrawable(R.drawable.field_bg));

        TextView icon = new TextView(this);
        icon.setText(ICONS[index]);
        icon.setTextSize(30);
        icon.setPadding(0, 0, px(16), 0);
        card.addView(icon);

        LinearLayout texts = new LinearLayout(this);
        texts.setOrientation(LinearLayout.VERTICAL);
        TextView name = new TextView(this);
        name.setText(NAMES[index]);
        name.setTextColor(Color.parseColor("#F5EFEA"));
        name.setTextSize(17);
        name.setTypeface(name.getTypeface(), android.graphics.Typeface.BOLD);
        TextView desc = new TextView(this);
        desc.setText(DESC[index]);
        desc.setTextColor(Color.parseColor("#B9ADA3"));
        desc.setTextSize(13);
        texts.addView(name);
        texts.addView(desc);
        card.addView(texts);

        card.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                openGame(index);
            }
        });
        return card;
    }

    private void openGame(int index) {
        stopGame();
        game = createGame(index);
        title.setText(NAMES[index]);
        content.removeAllViews();
        content.addView(game, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        gameBar.setVisibility(View.VISIBLE);
        statusView.setText("");
        scoreView = null;
    }

    private GameView createGame(int index) {
        switch (index) {
            case 0: return new SnakeView(this, this);
            case 1: return new Game2048View(this, this);
            case 2: return new BreakoutView(this, this);
            case 3: return new MemoryView(this, this);
            default: return new TicTacToeView(this, this);
        }
    }

    private void stopGame() {
        if (game != null) {
            game.stop();
            game = null;
        }
    }

    @Override
    public void onScore(final String label) {
        runOnUiThread(new Runnable() {
            public void run() {
                title.setText(label);
            }
        });
    }

    @Override
    public void onStatus(final String message) {
        runOnUiThread(new Runnable() {
            public void run() {
                statusView.setText(message);
            }
        });
    }

    @Override
    public void onBackPressed() {
        if (game != null) {
            showLauncher();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (game != null) game.stop();
    }

    private int px(int dp) {
        return Math.round(dp * density);
    }
}
