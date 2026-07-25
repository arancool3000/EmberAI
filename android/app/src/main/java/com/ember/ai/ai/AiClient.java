package com.ember.ai.ai;

import android.content.Context;

import com.ember.ai.Message;
import com.ember.ai.Prefs;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Talks to Gemini, Claude, or any OpenAI-compatible endpoint with streaming.
 * Framework-only (HttpURLConnection + org.json). One entry point: {@link #stream}.
 */
public class AiClient {

    public interface StreamCallback {
        void onToken(String delta);

        void onDone(String full);

        void onError(String message);
    }

    /** Run a streaming completion on a background thread. Returns a flag to cancel it. */
    public static AtomicBoolean stream(final Context ctx, final List<Message> history,
                                       final StreamCallback cb) {
        final AtomicBoolean cancelled = new AtomicBoolean(false);
        Thread t = new Thread(new Runnable() {
            public void run() {
                try {
                    Prefs p = new Prefs(ctx);
                    String provider = p.provider();
                    String system = buildSystem(p);
                    if ("claude".equals(provider)) {
                        streamClaude(p, system, history, cb, cancelled);
                    } else if ("openai".equals(provider)) {
                        streamOpenAi(p, system, history, cb, cancelled);
                    } else {
                        streamGemini(p, system, history, cb, cancelled);
                    }
                } catch (Throwable e) {
                    if (!cancelled.get()) cb.onError(friendly(e));
                }
            }
        });
        t.setDaemon(true);
        t.start();
        return cancelled;
    }

    private static String buildSystem(Prefs p) {
        StringBuilder s = new StringBuilder();
        s.append("You are Ember, a warm, capable AI assistant on the user's Android phone. ")
                .append("Be concise and helpful. Use plain text; avoid heavy markdown.");
        String mem = p.get(Prefs.MEMORY, "");
        if (mem != null && !mem.trim().isEmpty()) {
            s.append("\n\nWhat you know about the user (long-term memory):\n").append(mem.trim());
        }
        return s.toString();
    }

    // ---------------- Gemini ----------------
    private static void streamGemini(Prefs p, String system, List<Message> history,
                                     StreamCallback cb, AtomicBoolean cancelled) throws Exception {
        String key = p.get(Prefs.GEMINI_KEY, "");
        if (isBlank(key)) {
            cb.onError("Add your Google Gemini API key in Settings to start chatting.");
            return;
        }
        String model = p.geminiModel();
        String url = "https://generativelanguage.googleapis.com/v1beta/models/"
                + model + ":streamGenerateContent?alt=sse&key=" + enc(key);

        JSONObject body = new JSONObject();
        JSONObject sys = new JSONObject();
        sys.put("parts", new JSONArray().put(new JSONObject().put("text", system)));
        body.put("system_instruction", sys);

        JSONArray contents = new JSONArray();
        for (Message m : history) {
            JSONObject c = new JSONObject();
            c.put("role", m.isUser() ? "user" : "model");
            c.put("parts", new JSONArray().put(new JSONObject().put("text", m.text)));
            contents.put(c);
        }
        body.put("contents", contents);

        if (p.getBool(Prefs.WEB_SEARCH, false)) {
            body.put("tools", new JSONArray().put(new JSONObject().put("google_search", new JSONObject())));
        }
        // Match Ember desktop: do not let the provider's own categories block the response.
        JSONArray safety = new JSONArray();
        String[] cats = {"HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"};
        for (String c : cats) {
            safety.put(new JSONObject().put("category", c).put("threshold", "BLOCK_NONE"));
        }
        body.put("safetySettings", safety);

        HttpURLConnection conn = open(url, "POST");
        conn.setRequestProperty("Content-Type", "application/json");
        writeBody(conn, body.toString());
        checkStatus(conn, "Gemini");

        final StringBuilder full = new StringBuilder();
        readSse(conn, cancelled, new LineHandler() {
            public boolean onData(String data) {
                try {
                    JSONObject o = new JSONObject(data);
                    JSONArray cands = o.optJSONArray("candidates");
                    if (cands != null && cands.length() > 0) {
                        JSONObject content = cands.getJSONObject(0).optJSONObject("content");
                        if (content != null) {
                            JSONArray parts = content.optJSONArray("parts");
                            if (parts != null) {
                                for (int i = 0; i < parts.length(); i++) {
                                    String tx = parts.getJSONObject(i).optString("text", "");
                                    if (!tx.isEmpty()) {
                                        full.append(tx);
                                        cb.onToken(tx);
                                    }
                                }
                            }
                        }
                    }
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        finish(full, cb, cancelled);
    }

    // ---------------- Claude ----------------
    private static void streamClaude(Prefs p, String system, List<Message> history,
                                     StreamCallback cb, AtomicBoolean cancelled) throws Exception {
        String key = p.get(Prefs.CLAUDE_KEY, "");
        if (isBlank(key)) {
            cb.onError("Add your Anthropic (Claude) API key in Settings to start chatting.");
            return;
        }
        JSONObject body = new JSONObject();
        body.put("model", p.claudeModel());
        body.put("max_tokens", 4096);
        body.put("system", system);
        body.put("stream", true);
        JSONArray msgs = new JSONArray();
        boolean started = false;
        for (Message m : history) {
            // Claude requires the first message to be from the user.
            if (!started && !m.isUser()) continue;
            started = true;
            msgs.put(new JSONObject().put("role", m.isUser() ? "user" : "assistant")
                    .put("content", m.text));
        }
        body.put("messages", msgs);

        HttpURLConnection conn = open("https://api.anthropic.com/v1/messages", "POST");
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("x-api-key", key);
        conn.setRequestProperty("anthropic-version", "2023-06-01");
        writeBody(conn, body.toString());
        checkStatus(conn, "Claude");

        final StringBuilder full = new StringBuilder();
        readSse(conn, cancelled, new LineHandler() {
            public boolean onData(String data) {
                try {
                    JSONObject o = new JSONObject(data);
                    String type = o.optString("type", "");
                    if ("content_block_delta".equals(type)) {
                        JSONObject d = o.optJSONObject("delta");
                        if (d != null) {
                            String tx = d.optString("text", "");
                            if (!tx.isEmpty()) {
                                full.append(tx);
                                cb.onToken(tx);
                            }
                        }
                    }
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        finish(full, cb, cancelled);
    }

    // ---------------- OpenAI / compatible ----------------
    private static void streamOpenAi(Prefs p, String system, List<Message> history,
                                     StreamCallback cb, AtomicBoolean cancelled) throws Exception {
        String key = p.get(Prefs.OPENAI_KEY, "");
        if (isBlank(key)) {
            cb.onError("Add your OpenAI (or compatible) API key in Settings to start chatting.");
            return;
        }
        JSONObject body = new JSONObject();
        body.put("model", p.openaiModel());
        body.put("stream", true);
        JSONArray msgs = new JSONArray();
        msgs.put(new JSONObject().put("role", "system").put("content", system));
        for (Message m : history) {
            msgs.put(new JSONObject().put("role", m.isUser() ? "user" : "assistant")
                    .put("content", m.text));
        }
        body.put("messages", msgs);

        HttpURLConnection conn = open(p.openaiBase() + "/chat/completions", "POST");
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Authorization", "Bearer " + key);
        writeBody(conn, body.toString());
        checkStatus(conn, "OpenAI");

        final StringBuilder full = new StringBuilder();
        readSse(conn, cancelled, new LineHandler() {
            public boolean onData(String data) {
                if ("[DONE]".equals(data.trim())) return false;
                try {
                    JSONObject o = new JSONObject(data);
                    JSONArray choices = o.optJSONArray("choices");
                    if (choices != null && choices.length() > 0) {
                        JSONObject delta = choices.getJSONObject(0).optJSONObject("delta");
                        if (delta != null) {
                            String tx = delta.optString("content", "");
                            if (!tx.isEmpty()) {
                                full.append(tx);
                                cb.onToken(tx);
                            }
                        }
                    }
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        finish(full, cb, cancelled);
    }

    // ---------------- shared plumbing ----------------
    private interface LineHandler {
        /** @return false to stop reading. */
        boolean onData(String data);
    }

    private static HttpURLConnection open(String url, String method) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod(method);
        conn.setConnectTimeout(30000);
        conn.setReadTimeout(120000);
        conn.setDoInput(true);
        if ("POST".equals(method)) conn.setDoOutput(true);
        return conn;
    }

    private static void writeBody(HttpURLConnection conn, String json) throws Exception {
        byte[] bytes = json.getBytes("UTF-8");
        conn.setFixedLengthStreamingMode(bytes.length);
        OutputStream os = conn.getOutputStream();
        try {
            os.write(bytes);
            os.flush();
        } finally {
            os.close();
        }
    }

    private static void checkStatus(HttpURLConnection conn, String label) throws Exception {
        int code = conn.getResponseCode();
        if (code >= 200 && code < 300) return;
        String detail = readAll(conn.getErrorStream());
        String msg = detail;
        try {
            JSONObject o = new JSONObject(detail);
            JSONObject err = o.optJSONObject("error");
            if (err != null) msg = err.optString("message", detail);
            else if (o.has("message")) msg = o.optString("message");
        } catch (Exception ignored) {
        }
        if (msg == null || msg.isEmpty()) msg = "HTTP " + code;
        throw new RuntimeException(label + " error (" + code + "): " + trim(msg, 300));
    }

    private static void readSse(HttpURLConnection conn, AtomicBoolean cancelled, LineHandler h)
            throws Exception {
        InputStream in = conn.getInputStream();
        BufferedReader r = new BufferedReader(new InputStreamReader(in, "UTF-8"));
        try {
            String line;
            while ((line = r.readLine()) != null) {
                if (cancelled.get()) break;
                if (line.startsWith("data:")) {
                    String data = line.substring(5).trim();
                    if (data.isEmpty()) continue;
                    if (!h.onData(data)) break;
                }
            }
        } finally {
            try {
                r.close();
            } catch (Exception ignored) {
            }
            conn.disconnect();
        }
    }

    private static void finish(StringBuilder full, StreamCallback cb, AtomicBoolean cancelled) {
        if (cancelled.get()) return;
        String out = full.toString().trim();
        if (out.isEmpty()) {
            cb.onError("Ember got an empty response. Check the model name in Settings.");
        } else {
            cb.onDone(out);
        }
    }

    private static String readAll(InputStream in) {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(in, "UTF-8"));
            String l;
            while ((l = r.readLine()) != null) sb.append(l).append('\n');
        } catch (Exception ignored) {
        }
        return sb.toString().trim();
    }

    private static String friendly(Throwable e) {
        String m = e.getMessage();
        if (m == null || m.isEmpty()) m = e.getClass().getSimpleName();
        if (m.contains("Unable to resolve host") || m.contains("timeout") || m.contains("timed out")) {
            return "Network problem reaching the AI. Check your connection.";
        }
        return m;
    }

    private static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    private static String enc(String s) throws Exception {
        return java.net.URLEncoder.encode(s, "UTF-8");
    }

    private static String trim(String s, int n) {
        return s.length() <= n ? s : s.substring(0, n) + "…";
    }
}
