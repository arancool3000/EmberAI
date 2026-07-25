package com.ember.ai;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.Toast;

public class SettingsActivity extends Activity {

    private static final String[] PROVIDER_KEYS = {"gemini", "claude", "openai"};
    private static final String[] PROVIDER_LABELS = {"Google Gemini", "Anthropic Claude", "OpenAI / compatible"};

    private Prefs prefs;
    private Spinner provider;
    private EditText geminiKey, claudeKey, openaiKey, openaiBase;
    private EditText geminiModel, claudeModel, openaiModel, memory;
    private Switch webSearch, readAloud;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_settings);
        prefs = new Prefs(this);

        provider = (Spinner) findViewById(R.id.provider);
        geminiKey = (EditText) findViewById(R.id.gemini_key);
        claudeKey = (EditText) findViewById(R.id.claude_key);
        openaiKey = (EditText) findViewById(R.id.openai_key);
        openaiBase = (EditText) findViewById(R.id.openai_base);
        geminiModel = (EditText) findViewById(R.id.gemini_model);
        claudeModel = (EditText) findViewById(R.id.claude_model);
        openaiModel = (EditText) findViewById(R.id.openai_model);
        memory = (EditText) findViewById(R.id.memory);
        webSearch = (Switch) findViewById(R.id.web_search);
        readAloud = (Switch) findViewById(R.id.read_aloud);

        ArrayAdapter<String> pa = new ArrayAdapter<String>(this,
                android.R.layout.simple_spinner_item, PROVIDER_LABELS);
        pa.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        provider.setAdapter(pa);

        // Load current values.
        provider.setSelection(indexOf(prefs.provider()));
        geminiKey.setText(prefs.get(Prefs.GEMINI_KEY, ""));
        claudeKey.setText(prefs.get(Prefs.CLAUDE_KEY, ""));
        openaiKey.setText(prefs.get(Prefs.OPENAI_KEY, ""));
        openaiBase.setText(prefs.get(Prefs.OPENAI_BASE, ""));
        geminiModel.setText(prefs.geminiModel());
        claudeModel.setText(prefs.claudeModel());
        openaiModel.setText(prefs.openaiModel());
        memory.setText(prefs.get(Prefs.MEMORY, ""));
        webSearch.setChecked(prefs.getBool(Prefs.WEB_SEARCH, false));
        readAloud.setChecked(prefs.getBool(Prefs.READ_ALOUD, false));

        Button save = (Button) findViewById(R.id.save);
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                saveAll();
            }
        });
    }

    private void saveAll() {
        int i = provider.getSelectedItemPosition();
        if (i < 0 || i >= PROVIDER_KEYS.length) i = 0;
        prefs.put(Prefs.PROVIDER, PROVIDER_KEYS[i]);
        prefs.put(Prefs.GEMINI_KEY, geminiKey.getText().toString().trim());
        prefs.put(Prefs.CLAUDE_KEY, claudeKey.getText().toString().trim());
        prefs.put(Prefs.OPENAI_KEY, openaiKey.getText().toString().trim());
        prefs.put(Prefs.OPENAI_BASE, openaiBase.getText().toString().trim());
        prefs.put(Prefs.GEMINI_MODEL, geminiModel.getText().toString().trim());
        prefs.put(Prefs.CLAUDE_MODEL, claudeModel.getText().toString().trim());
        prefs.put(Prefs.OPENAI_MODEL, openaiModel.getText().toString().trim());
        prefs.put(Prefs.MEMORY, memory.getText().toString());
        prefs.putBool(Prefs.WEB_SEARCH, webSearch.isChecked());
        prefs.putBool(Prefs.READ_ALOUD, readAloud.isChecked());
        Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show();
        finish();
    }

    private int indexOf(String key) {
        for (int i = 0; i < PROVIDER_KEYS.length; i++) {
            if (PROVIDER_KEYS[i].equals(key)) return i;
        }
        return 0;
    }
}
