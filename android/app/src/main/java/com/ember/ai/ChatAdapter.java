package com.ember.ai;

import android.content.Context;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.List;

/** Renders the transcript: user bubbles align right, Ember bubbles align left. */
public class ChatAdapter extends BaseAdapter {

    private final Context ctx;
    private final List<Message> data;
    private WordFilter filter;

    public ChatAdapter(Context ctx, List<Message> data, WordFilter filter) {
        this.ctx = ctx;
        this.data = data;
        this.filter = filter;
    }

    public void setFilter(WordFilter f) {
        this.filter = f;
        notifyDataSetChanged();
    }

    public int getCount() {
        return data.size();
    }

    public Object getItem(int position) {
        return data.get(position);
    }

    public long getItemId(int position) {
        return position;
    }

    public View getView(int position, View convertView, ViewGroup parent) {
        View v = convertView;
        if (v == null) {
            v = LayoutInflater.from(ctx).inflate(R.layout.item_message, parent, false);
        }
        Message m = data.get(position);
        LinearLayout row = (LinearLayout) v.findViewById(R.id.row);
        TextView who = (TextView) v.findViewById(R.id.who);
        TextView bubble = (TextView) v.findViewById(R.id.bubble);

        boolean user = m.isUser();
        row.setGravity(user ? Gravity.END : Gravity.START);
        who.setText(user ? "You" : "Ember");
        who.setGravity(user ? Gravity.END : Gravity.START);
        who.setTextColor(ctx.getResources().getColor(
                user ? R.color.ember_gold : R.color.ember_flame));
        bubble.setBackgroundResource(user ? R.drawable.bubble_user : R.drawable.bubble_ai);

        String text = m.text == null ? "" : m.text;
        if (filter != null) text = filter.apply(text);
        bubble.setText(text);
        return v;
    }
}
