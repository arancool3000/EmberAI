package com.ember.ai;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * On-device settings. Everything (API keys, models, memory, word-filter choices)
 * is stored only in this app's private SharedPreferences — Ember has no servers.
 */
public class Prefs {
    private static final String FILE = "ember_prefs";

    public static final String PROVIDER = "provider";          // gemini | claude | openai
    public static final String GEMINI_KEY = "gemini_key";
    public static final String CLAUDE_KEY = "claude_key";
    public static final String OPENAI_KEY = "openai_key";
    public static final String OPENAI_BASE = "openai_base";
    public static final String GEMINI_MODEL = "gemini_model";
    public static final String CLAUDE_MODEL = "claude_model";
    public static final String OPENAI_MODEL = "openai_model";
    public static final String WEB_SEARCH = "web_search";
    public static final String READ_ALOUD = "read_aloud";
    public static final String MEMORY = "memory";

    // Word filter
    public static final String FILTER_UNBLOCK = "filter_unblock_all";
    public static final String FILTER_CUSTOM = "filter_custom_words";
    public static final String AGE_CONFIRMED = "age_confirmed_18";

    private final SharedPreferences sp;

    public Prefs(Context c) {
        sp = c.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    public String get(String k, String def) {
        return sp.getString(k, def);
    }

    public void put(String k, String v) {
        sp.edit().putString(k, v == null ? "" : v).apply();
    }

    public boolean getBool(String k, boolean def) {
        return sp.getBoolean(k, def);
    }

    public void putBool(String k, boolean v) {
        sp.edit().putBoolean(k, v).apply();
    }

    /** Sensible defaults so the app is usable the moment a key is pasted. */
    public String geminiModel() {
        return orDefault(GEMINI_MODEL, "gemini-2.0-flash");
    }

    public String claudeModel() {
        return orDefault(CLAUDE_MODEL, "claude-3-5-sonnet-latest");
    }

    public String openaiModel() {
        return orDefault(OPENAI_MODEL, "gpt-4o-mini");
    }

    public String openaiBase() {
        String b = get(OPENAI_BASE, "");
        if (b == null || b.trim().isEmpty()) return "https://api.openai.com/v1";
        b = b.trim();
        while (b.endsWith("/")) b = b.substring(0, b.length() - 1);
        return b;
    }

    public String provider() {
        return orDefault(PROVIDER, "gemini");
    }

    private String orDefault(String k, String def) {
        String v = get(k, "");
        return (v == null || v.trim().isEmpty()) ? def : v.trim();
    }
}
