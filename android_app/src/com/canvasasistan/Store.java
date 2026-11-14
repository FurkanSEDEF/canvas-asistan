package com.canvasasistan;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Ayarlar. Token, Android Keystore (donanım destekli) ile şifrelenir; telefondan dışarı çıkmaz. */
final class Store {
    private static final String ALIAS = "canvas_token_key";

    static SharedPreferences p(Context c) {
        return c.getSharedPreferences("cfg", Context.MODE_PRIVATE);
    }

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (!ks.containsAlias(ALIAS)) {
            KeyGenerator g = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            g.init(new KeyGenParameterSpec.Builder(ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build());
            return g.generateKey();
        }
        return (SecretKey) ks.getKey(ALIAS, null);
    }

    static void saveLogin(Context c, String baseUrl, String token) throws Exception {
        Cipher ci = Cipher.getInstance("AES/GCM/NoPadding");
        ci.init(Cipher.ENCRYPT_MODE, key());
        byte[] ct = ci.doFinal(token.getBytes(StandardCharsets.UTF_8));
        String blob = Base64.encodeToString(ci.getIV(), Base64.NO_WRAP) + ":" + Base64.encodeToString(ct, Base64.NO_WRAP);
        p(c).edit().putString("base", baseUrl).putString("tok", blob).apply();
    }

    static String token(Context c) {
        try {
            String blob = p(c).getString("tok", null);
            if (blob == null) return null;
            String[] parts = blob.split(":");
            Cipher ci = Cipher.getInstance("AES/GCM/NoPadding");
            ci.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
            return new String(ci.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return null;
        }
    }

    static String base(Context c) {
        return p(c).getString("base", null);
    }

    static boolean loggedIn(Context c) {
        return base(c) != null && token(c) != null;
    }

    static void logout(Context c) {
        p(c).edit().clear().apply();
    }

    // Değişiklik tespiti için önceki durum (bildirimlerin tekrarlanmaması)
    static JSONObject state(Context c) {
        try {
            return new JSONObject(p(c).getString("state", "{}"));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    static void saveState(Context c, JSONObject s) {
        p(c).edit().putString("state", s.toString()).apply();
    }

    static JSONObject cache(Context c) {
        try {
            String s = p(c).getString("cache", null);
            return s == null ? null : new JSONObject(s);
        } catch (Exception e) {
            return null;
        }
    }

    static void saveCache(Context c, JSONObject d) {
        p(c).edit().putString("cache", d.toString()).apply();
    }
}
