package com.ember.ai;

import org.json.JSONException;
import org.json.JSONObject;

/** A single chat message. role is "user" or "assistant". */
public class Message {
    public String role;
    public String text;

    public Message(String role, String text) {
        this.role = role;
        this.text = text;
    }

    public boolean isUser() {
        return "user".equals(role);
    }

    public JSONObject toJson() {
        JSONObject o = new JSONObject();
        try {
            o.put("role", role);
            o.put("text", text);
        } catch (JSONException ignored) {
        }
        return o;
    }

    public static Message fromJson(JSONObject o) {
        return new Message(o.optString("role", "assistant"), o.optString("text", ""));
    }
}
