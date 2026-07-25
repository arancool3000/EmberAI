package com.ember.ai;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;

/** Persists conversations as a single JSON file in the app's private storage. */
public class ChatStore {

    public static class Chat {
        public String id;
        public String title;
        public long updated;
        public final List<Message> messages = new ArrayList<Message>();

        public Chat(String id, String title, long updated) {
            this.id = id;
            this.title = title;
            this.updated = updated;
        }
    }

    private final File file;

    public ChatStore(Context c) {
        file = new File(c.getFilesDir(), "chats.json");
    }

    public List<Chat> load() {
        List<Chat> out = new ArrayList<Chat>();
        if (!file.exists()) return out;
        try {
            String raw = readFile(file);
            JSONArray arr = new JSONArray(raw);
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                Chat ch = new Chat(o.optString("id"), o.optString("title", "New chat"),
                        o.optLong("updated", 0));
                JSONArray msgs = o.optJSONArray("messages");
                if (msgs != null) {
                    for (int j = 0; j < msgs.length(); j++) {
                        ch.messages.add(Message.fromJson(msgs.getJSONObject(j)));
                    }
                }
                out.add(ch);
            }
        } catch (Exception e) {
            // Corrupt store: start fresh rather than crash.
        }
        return out;
    }

    public void saveAll(List<Chat> chats) {
        JSONArray arr = new JSONArray();
        try {
            for (Chat ch : chats) {
                if (ch.messages.isEmpty()) continue; // don't persist empty chats
                JSONObject o = new JSONObject();
                o.put("id", ch.id);
                o.put("title", ch.title);
                o.put("updated", ch.updated);
                JSONArray msgs = new JSONArray();
                for (Message m : ch.messages) msgs.put(m.toJson());
                o.put("messages", msgs);
                arr.put(o);
            }
        } catch (JSONException e) {
            return;
        }
        FileOutputStream fos = null;
        try {
            fos = new FileOutputStream(file);
            fos.write(arr.toString().getBytes("UTF-8"));
        } catch (Exception e) {
            // best-effort persistence
        } finally {
            if (fos != null) {
                try {
                    fos.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    private static String readFile(File f) throws Exception {
        byte[] buf = new byte[(int) f.length()];
        java.io.FileInputStream in = new java.io.FileInputStream(f);
        try {
            int off = 0, r;
            while (off < buf.length && (r = in.read(buf, off, buf.length - off)) > 0) off += r;
        } finally {
            in.close();
        }
        return new String(buf, "UTF-8");
    }
}
