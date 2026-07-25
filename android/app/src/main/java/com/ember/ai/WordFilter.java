package com.ember.ai;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Client-side word filter. By default a short built-in list of strong words is
 * masked with asterisks in the transcript. The user can, per the new-chat dialog:
 *   - turn the filter off entirely ("unblock all words"), gated by an 18+ confirmation, or
 *   - add their own words to the block list.
 * Nothing here is sent anywhere; it only changes how text is displayed.
 */
public class WordFilter {

    // A deliberately small built-in list. The point of the feature is the toggle,
    // not an exhaustive dictionary. Users add their own via the new-chat dialog.
    private static final String[] BUILTIN = {
            "fuck", "shit", "bitch", "cunt", "asshole", "bastard", "dick", "slut", "whore"
    };

    private final boolean unblockAll;
    private final Pattern pattern; // null when nothing to filter

    public WordFilter(boolean unblockAll, String customCommaSeparated) {
        this.unblockAll = unblockAll;
        this.pattern = unblockAll ? null : buildPattern(customCommaSeparated);
    }

    /** Convenience: build from current prefs. */
    public static WordFilter from(Prefs p) {
        boolean unblock = p.getBool(Prefs.FILTER_UNBLOCK, false) && p.getBool(Prefs.AGE_CONFIRMED, false);
        return new WordFilter(unblock, p.get(Prefs.FILTER_CUSTOM, ""));
    }

    private static Pattern buildPattern(String custom) {
        LinkedHashSet<String> words = new LinkedHashSet<String>();
        for (String w : BUILTIN) words.add(w);
        if (custom != null) {
            for (String raw : custom.split("[,\\n]")) {
                String w = raw.trim().toLowerCase();
                if (!w.isEmpty()) words.add(w);
            }
        }
        if (words.isEmpty()) return null;
        List<String> quoted = new ArrayList<String>();
        for (String w : words) quoted.add(Pattern.quote(w));
        // \b word boundaries, case-insensitive.
        String body = join(quoted, "|");
        return Pattern.compile("\\b(?:" + body + ")\\b", Pattern.CASE_INSENSITIVE);
    }

    /** Return the text with any blocked words masked, or unchanged if unblocked. */
    public String apply(String text) {
        if (unblockAll || pattern == null || text == null || text.isEmpty()) return text;
        Matcher m = pattern.matcher(text);
        StringBuffer sb = new StringBuffer();
        while (m.find()) {
            m.appendReplacement(sb, Matcher.quoteReplacement(mask(m.group().length())));
        }
        m.appendTail(sb);
        return sb.toString();
    }

    private static String mask(int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append('*');
        return b.toString();
    }

    private static String join(List<String> parts, String sep) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < parts.size(); i++) {
            if (i > 0) b.append(sep);
            b.append(parts.get(i));
        }
        return b.toString();
    }
}
