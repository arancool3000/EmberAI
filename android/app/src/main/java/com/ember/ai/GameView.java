package com.ember.ai;

import android.content.Context;
import android.view.View;

/** Base for all arcade games. Subclasses draw themselves and handle touch. */
public abstract class GameView extends View {

    public interface Host {
        void onScore(String label);

        void onStatus(String message);
    }

    protected final Host host;

    public GameView(Context c, Host host) {
        super(c);
        this.host = host;
        setFocusable(true);
    }

    /** Reset to a fresh game. */
    public abstract void restart();

    /** Called when the game view is being removed (stop timers). */
    public void stop() {
    }

    protected float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }

    protected void score(String label) {
        if (host != null) host.onScore(label);
    }

    protected void status(String message) {
        if (host != null) host.onStatus(message);
    }
}
