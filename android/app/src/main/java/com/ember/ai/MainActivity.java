package com.ember.ai;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.ListView;
import android.widget.PopupMenu;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import com.ember.ai.ai.AiClient;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

public class MainActivity extends Activity {

    private static final int REQ_MIC = 101;

    private ListView list;
    private ChatAdapter adapter;
    private EditText input;
    private ImageButton btnSend, btnMic, btnMenu, btnNew;
    private TextView typing, modelChip;

    private Prefs prefs;
    private Voice voice;
    private ChatStore store;
    private final List<ChatStore.Chat> allChats = new ArrayList<ChatStore.Chat>();
    private ChatStore.Chat current;

    private AtomicBoolean activeStream;
    private boolean streaming;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        prefs = new Prefs(this);
        voice = new Voice(this);
        store = new ChatStore(this);

        list = (ListView) findViewById(R.id.chat_list);
        input = (EditText) findViewById(R.id.input);
        btnSend = (ImageButton) findViewById(R.id.btn_send);
        btnMic = (ImageButton) findViewById(R.id.btn_mic);
        btnMenu = (ImageButton) findViewById(R.id.btn_menu);
        btnNew = (ImageButton) findViewById(R.id.btn_new);
        typing = (TextView) findViewById(R.id.typing);
        modelChip = (TextView) findViewById(R.id.model_chip);

        allChats.addAll(store.load());
        if (!allChats.isEmpty()) {
            current = allChats.get(allChats.size() - 1);
        } else {
            current = newChat();
        }

        adapter = new ChatAdapter(this, current.messages, WordFilter.from(prefs));
        list.setAdapter(adapter);
        scrollToEnd();

        btnSend.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                if (streaming) cancelStream();
                else send();
            }
        });
        btnMic.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                onMic();
            }
        });
        btnNew.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                showNewChatDialog();
            }
        });
        btnMenu.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                showMenu(v);
            }
        });

        if (current.messages.isEmpty()) greet();
    }

    @Override
    protected void onResume() {
        super.onResume();
        adapter.setFilter(WordFilter.from(prefs));
        String pv = prefs.provider();
        modelChip.setText("claude".equals(pv) ? "Claude" : "openai".equals(pv) ? "OpenAI" : "Gemini");
    }

    @Override
    protected void onPause() {
        super.onPause();
        persist();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (voice != null) voice.shutdown();
    }

    // ---------------- chat lifecycle ----------------
    private ChatStore.Chat newChat() {
        String id = "chat_" + System.currentTimeMillis();
        ChatStore.Chat c = new ChatStore.Chat(id, "New chat", System.currentTimeMillis());
        allChats.add(c);
        return c;
    }

    private void greet() {
        current.messages.add(new Message("assistant",
                "Hi, I'm Ember — your AI on this phone. Ask me anything, tap the mic to talk, "
                        + "or open the menu for the Arcade. Add an API key in Settings to begin."));
        adapter.notifyDataSetChanged();
        scrollToEnd();
    }

    private void send() {
        String text = input.getText().toString().trim();
        if (TextUtils.isEmpty(text)) return;
        if (!hasAnyKey()) {
            Toast.makeText(this, "Add an API key in Settings first.", Toast.LENGTH_LONG).show();
            openSettings();
            return;
        }
        input.setText("");
        hideKeyboard();
        current.messages.add(new Message("user", text));
        if ("New chat".equals(current.title)) {
            current.title = text.length() > 40 ? text.substring(0, 40) : text;
        }
        Message assistant = new Message("assistant", "");
        current.messages.add(assistant);
        adapter.notifyDataSetChanged();
        scrollToEnd();
        setStreaming(true);

        // History excludes the empty assistant placeholder we just added.
        final List<Message> history = new ArrayList<Message>(
                current.messages.subList(0, current.messages.size() - 1));
        final Message target = assistant;
        activeStream = AiClient.stream(this, history, new AiClient.StreamCallback() {
            public void onToken(final String delta) {
                ui.post(new Runnable() {
                    public void run() {
                        target.text = target.text + delta;
                        adapter.notifyDataSetChanged();
                        scrollToEnd();
                    }
                });
            }

            public void onDone(final String full) {
                ui.post(new Runnable() {
                    public void run() {
                        target.text = full;
                        current.updated = System.currentTimeMillis();
                        adapter.notifyDataSetChanged();
                        scrollToEnd();
                        setStreaming(false);
                        persist();
                        if (prefs.getBool(Prefs.READ_ALOUD, false)) {
                            voice.speak(WordFilter.from(prefs).apply(full));
                        }
                    }
                });
            }

            public void onError(final String message) {
                ui.post(new Runnable() {
                    public void run() {
                        if (target.text.isEmpty()) {
                            target.text = "⚠️ " + message;
                        } else {
                            target.text = target.text + "\n\n⚠️ " + message;
                        }
                        adapter.notifyDataSetChanged();
                        scrollToEnd();
                        setStreaming(false);
                        persist();
                    }
                });
            }
        });
    }

    private void cancelStream() {
        if (activeStream != null) activeStream.set(true);
        setStreaming(false);
    }

    private void setStreaming(boolean on) {
        streaming = on;
        typing.setVisibility(on ? View.VISIBLE : View.GONE);
        btnSend.setImageResource(on ? R.drawable.ic_stop : R.drawable.ic_send);
    }

    // ---------------- voice ----------------
    private void onMic() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startListening();
    }

    private void startListening() {
        btnMic.setBackgroundResource(R.drawable.btn_mic_active);
        voice.listen(new Voice.Listener() {
            public void onResult(String text) {
                input.setText(text);
                input.setSelection(text.length());
                btnMic.setBackgroundResource(R.drawable.btn_circle_surface);
                send();
            }

            public void onError(String message) {
                btnMic.setBackgroundResource(R.drawable.btn_circle_surface);
                Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show();
            }

            public void onState(boolean listening) {
                btnMic.setBackgroundResource(listening
                        ? R.drawable.btn_mic_active : R.drawable.btn_circle_surface);
            }
        });
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == REQ_MIC) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startListening();
            } else {
                Toast.makeText(this, "Microphone permission is needed for voice.",
                        Toast.LENGTH_SHORT).show();
            }
        }
    }

    // ---------------- new-chat word-filter dialog ----------------
    private void showNewChatDialog() {
        View v = getLayoutInflater().inflate(R.layout.dialog_new_chat, null);
        final EditText custom = (EditText) v.findViewById(R.id.custom_words);
        final Switch unblock = (Switch) v.findViewById(R.id.unblock_all);
        final CheckBox age = (CheckBox) v.findViewById(R.id.age_confirm);

        custom.setText(prefs.get(Prefs.FILTER_CUSTOM, ""));
        unblock.setChecked(prefs.getBool(Prefs.FILTER_UNBLOCK, false));
        age.setChecked(prefs.getBool(Prefs.AGE_CONFIRMED, false));

        final AlertDialog dlg = new AlertDialog.Builder(this, R.style.EmberDialog)
                .setView(v)
                .setPositiveButton(R.string.start_chat, null)
                .setNegativeButton(R.string.cancel, null)
                .create();
        dlg.show();
        // Override positive so we can validate the age gate before dismissing.
        dlg.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(new View.OnClickListener() {
            public void onClick(View b) {
                boolean wantUnblock = unblock.isChecked();
                boolean confirmed = age.isChecked();
                if (wantUnblock && !confirmed) {
                    Toast.makeText(MainActivity.this, R.string.age_required, Toast.LENGTH_LONG).show();
                    return;
                }
                prefs.put(Prefs.FILTER_CUSTOM, custom.getText().toString());
                prefs.putBool(Prefs.FILTER_UNBLOCK, wantUnblock);
                prefs.putBool(Prefs.AGE_CONFIRMED, confirmed);
                dlg.dismiss();
                startFreshChat();
            }
        });
    }

    private void startFreshChat() {
        cancelStream();
        persist();
        current = newChat();
        adapter = new ChatAdapter(this, current.messages, WordFilter.from(prefs));
        list.setAdapter(adapter);
        greet();
    }

    // ---------------- menu ----------------
    private void showMenu(View anchor) {
        PopupMenu pm = new PopupMenu(this, anchor);
        pm.getMenu().add(0, 1, 0, R.string.new_chat);
        pm.getMenu().add(0, 2, 1, R.string.history);
        pm.getMenu().add(0, 3, 2, "Arcade");
        pm.getMenu().add(0, 4, 3, R.string.clear_chat);
        pm.getMenu().add(0, 5, 4, R.string.settings);
        pm.setOnMenuItemClickListener(new PopupMenu.OnMenuItemClickListener() {
            public boolean onMenuItemClick(android.view.MenuItem item) {
                switch (item.getItemId()) {
                    case 1:
                        showNewChatDialog();
                        return true;
                    case 2:
                        showHistory();
                        return true;
                    case 3:
                        startActivity(new Intent(MainActivity.this, ArcadeActivity.class));
                        return true;
                    case 4:
                        clearCurrent();
                        return true;
                    case 5:
                        openSettings();
                        return true;
                }
                return false;
            }
        });
        pm.show();
    }

    private void showHistory() {
        persist();
        final List<ChatStore.Chat> withMsgs = new ArrayList<ChatStore.Chat>();
        for (ChatStore.Chat c : allChats) {
            if (!c.messages.isEmpty()) withMsgs.add(c);
        }
        if (withMsgs.isEmpty()) {
            Toast.makeText(this, "No past chats yet.", Toast.LENGTH_SHORT).show();
            return;
        }
        final String[] titles = new String[withMsgs.size()];
        for (int i = 0; i < withMsgs.size(); i++) {
            // newest first
            ChatStore.Chat c = withMsgs.get(withMsgs.size() - 1 - i);
            titles[i] = c.title;
        }
        new AlertDialog.Builder(this, R.style.EmberDialog)
                .setTitle("History")
                .setItems(titles, new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface d, int which) {
                        ChatStore.Chat c = withMsgs.get(withMsgs.size() - 1 - which);
                        openChat(c);
                    }
                })
                .setNegativeButton(R.string.cancel, null)
                .show();
    }

    private void openChat(ChatStore.Chat c) {
        cancelStream();
        current = c;
        adapter = new ChatAdapter(this, current.messages, WordFilter.from(prefs));
        list.setAdapter(adapter);
        scrollToEnd();
    }

    private void clearCurrent() {
        cancelStream();
        current.messages.clear();
        adapter.notifyDataSetChanged();
        persist();
        greet();
    }

    // ---------------- helpers ----------------
    private void openSettings() {
        startActivity(new Intent(this, SettingsActivity.class));
    }

    private boolean hasAnyKey() {
        String pv = prefs.provider();
        if ("claude".equals(pv)) return notBlank(prefs.get(Prefs.CLAUDE_KEY, ""));
        if ("openai".equals(pv)) return notBlank(prefs.get(Prefs.OPENAI_KEY, ""));
        return notBlank(prefs.get(Prefs.GEMINI_KEY, ""));
    }

    private boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty();
    }

    private void persist() {
        current.updated = System.currentTimeMillis();
        store.saveAll(allChats);
    }

    private void scrollToEnd() {
        list.post(new Runnable() {
            public void run() {
                int n = adapter.getCount();
                if (n > 0) list.setSelection(n - 1);
            }
        });
    }

    private void hideKeyboard() {
        try {
            InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            imm.hideSoftInputFromWindow(input.getWindowToken(), 0);
        } catch (Exception ignored) {
        }
    }
}
