package com.ember.ai;

import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;

import java.util.ArrayList;
import java.util.Locale;

/** Thin wrapper over the framework SpeechRecognizer (voice in) and TextToSpeech (voice out). */
public class Voice {

    public interface Listener {
        void onResult(String text);

        void onError(String message);

        void onState(boolean listening);
    }

    private final Context ctx;
    private SpeechRecognizer recognizer;
    private TextToSpeech tts;
    private boolean ttsReady;

    public Voice(Context ctx) {
        this.ctx = ctx.getApplicationContext();
    }

    public boolean recognitionAvailable() {
        return SpeechRecognizer.isRecognitionAvailable(ctx);
    }

    /** Must be called on the main thread. */
    public void listen(final Listener l) {
        if (!recognitionAvailable()) {
            l.onError("Speech recognition isn't available on this device.");
            return;
        }
        if (recognizer != null) {
            try {
                recognizer.destroy();
            } catch (Exception ignored) {
            }
        }
        recognizer = SpeechRecognizer.createSpeechRecognizer(ctx);
        recognizer.setRecognitionListener(new RecognitionListener() {
            public void onReadyForSpeech(Bundle params) {
                l.onState(true);
            }

            public void onBeginningOfSpeech() {
            }

            public void onRmsChanged(float rmsdB) {
            }

            public void onBufferReceived(byte[] buffer) {
            }

            public void onEndOfSpeech() {
                l.onState(false);
            }

            public void onError(int error) {
                l.onState(false);
                l.onError(errText(error));
            }

            public void onResults(Bundle results) {
                l.onState(false);
                ArrayList<String> list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list != null && !list.isEmpty()) l.onResult(list.get(0));
                else l.onError("Didn't catch that.");
            }

            public void onPartialResults(Bundle partialResults) {
            }

            public void onEvent(int eventType, Bundle params) {
            }
        });
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault());
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false);
        try {
            recognizer.startListening(i);
        } catch (Exception e) {
            l.onError("Couldn't start listening.");
        }
    }

    public void cancelListening() {
        if (recognizer != null) {
            try {
                recognizer.cancel();
            } catch (Exception ignored) {
            }
        }
    }

    public void speak(final String text) {
        if (text == null || text.trim().isEmpty()) return;
        if (tts == null) {
            tts = new TextToSpeech(ctx, new TextToSpeech.OnInitListener() {
                public void onInit(int status) {
                    ttsReady = (status == TextToSpeech.SUCCESS);
                    if (ttsReady) {
                        tts.setLanguage(Locale.getDefault());
                        doSpeak(text);
                    }
                }
            });
        } else if (ttsReady) {
            doSpeak(text);
        }
    }

    private void doSpeak(String text) {
        try {
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "ember-tts");
        } catch (Exception ignored) {
        }
    }

    public void stopSpeaking() {
        if (tts != null) {
            try {
                tts.stop();
            } catch (Exception ignored) {
            }
        }
    }

    public void shutdown() {
        if (recognizer != null) {
            try {
                recognizer.destroy();
            } catch (Exception ignored) {
            }
            recognizer = null;
        }
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Exception ignored) {
            }
            tts = null;
        }
    }

    private static String errText(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_AUDIO:
                return "Audio recording error.";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                return "Microphone permission is needed for voice input.";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                return "Network error during recognition.";
            case SpeechRecognizer.ERROR_NO_MATCH:
                return "Didn't catch that — try again.";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                return "No speech heard.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                return "Recognizer is busy.";
            default:
                return "Voice input error.";
        }
    }
}
