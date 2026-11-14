package com.canvasasistan;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationManager;
import android.app.job.JobScheduler;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.Html;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class Main extends Activity {
    static final int BLUE = 0xFF1E64C8, BG = 0xFFF2F4F8, TEXT = 0xFF1B1F27, MUTED = 0xFF6B7380,
            RED = 0xFFD32F2F, ORANGE = 0xFFEF6C00, GREEN = 0xFF2E7D32, GRAY = 0xFF78909C;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private JSONObject data;
    private int tab = 0;
    private TextView status;
    private LinearLayout list;
    private TextView[] tabViews;
    private boolean mainShown, refreshing;
    private long lastRefresh;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(BLUE);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        if (Store.loggedIn(this)) showMain(); else showLogin();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (mainShown && !refreshing && System.currentTimeMillis() - lastRefresh > 120000) refresh();
    }

    // ------------------------------------------------------------------ yardımcılar
    int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    GradientDrawable round(int color, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    TextView tv(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    LinearLayout box(int padDp) {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(dp(padDp), dp(padDp), dp(padDp), dp(padDp));
        return l;
    }

    LinearLayout.LayoutParams lp(int w, int h, int weight, int topDp) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h, weight);
        p.topMargin = dp(topDp);
        return p;
    }

    LinearLayout cardBox() {
        LinearLayout c = box(16);
        c.setBackground(round(0xFFFFFFFF, 14));
        c.setElevation(dp(2));
        return c;
    }

    // ------------------------------------------------------------------ giriş ekranı
    void showLogin() {
        mainShown = false;
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(BG);
        LinearLayout root = box(20);
        sv.addView(root);
        root.addView(tv("Canvas Asistanım", 28, BLUE, true));
        root.addView(tv("Teslimlerini ve duyuruları kaçırma.", 15, MUTED, false), lp(-2, -2, 0, 4));

        LinearLayout card = cardBox();
        root.addView(card, lp(-1, -2, 0, 20));
        card.addView(tv("Canvas adresi", 13, MUTED, true));
        EditText url = new EditText(this);
        url.setSingleLine();
        url.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        url.setText("https://canvas.agu.edu.tr");
        card.addView(url);
        card.addView(tv("Canvas token", 13, MUTED, true), lp(-2, -2, 0, 12));
        EditText tok = new EditText(this);
        tok.setSingleLine();
        tok.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tok.setHint("Token'ı buraya yapıştır");
        card.addView(tok);
        TextView err = tv("", 13, RED, false);
        card.addView(err, lp(-2, -2, 0, 8));
        Button go = new Button(this);
        go.setText("Bağlan");
        go.setTextColor(0xFFFFFFFF);
        go.setAllCaps(false);
        go.setBackground(round(BLUE, 24));
        card.addView(go, lp(-1, dp(48), 0, 12));

        LinearLayout help = cardBox();
        root.addView(help, lp(-1, -2, 0, 16));
        help.addView(tv("Token nasıl alınır?", 15, TEXT, true));
        help.addView(tv("1. Tarayıcıdan Canvas'a giriş yap\n2. Hesap → Ayarlar\n3. \"Approved Integrations\" → \"+ New Access Token\"\n"
                + "4. Amaç: Canvas Asistanım → Generate Token\n5. Çıkan uzun metni kopyalayıp yukarıya yapıştır.\n\n"
                + "Token'ı yalnızca bir kez girmen yeterli. Telefonda şifreli saklanır ve yalnızca kendi Canvas sunucuna gönderilir. "
                + "Uygulama sadece okur; hiçbir ödevi teslim etmez.", 13, MUTED, false), lp(-2, -2, 0, 8));

        go.setOnClickListener(v -> {
            String base = url.getText().toString().trim();
            String token = tok.getText().toString().trim();
            if (!base.startsWith("http")) base = "https://" + base;
            base = base.replaceAll("^(https?://[^/]+).*$", "$1");
            if (token.isEmpty()) {
                err.setText("Token gerekli.");
                return;
            }
            err.setText("");
            go.setEnabled(false);
            go.setText("Bağlanılıyor…");
            final String fb = base;
            io.execute(() -> {
                String msg = null;
                try {
                    new Canvas(fb, token).object("/api/v1/users/self");
                    Store.saveLogin(this, fb, token);
                } catch (Canvas.ApiException e) {
                    msg = e.code == 401 ? "Token geçersiz veya süresi dolmuş." : "Canvas hatası (HTTP " + e.code + ").";
                } catch (IllegalArgumentException e) {
                    msg = "Adres https:// ile başlamalı.";
                } catch (Exception e) {
                    msg = "Bağlanılamadı. İnternetini ve adresi kontrol et.";
                }
                final String m = msg;
                ui.post(() -> {
                    if (m == null) {
                        showMain();
                    } else {
                        err.setText(m);
                        go.setEnabled(true);
                        go.setText("Bağlan");
                    }
                });
            });
        });
        setContentView(sv);
    }

    // ------------------------------------------------------------------ ana ekran
    void showMain() {
        mainShown = true;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        LinearLayout header = box(16);
        header.setBackgroundColor(BLUE);
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(tv("Canvas Asistanım", 22, 0xFFFFFFFF, true), new LinearLayout.LayoutParams(0, -2, 1));
        TextView ref = tv("Yenile", 14, 0xFFFFFFFF, true);
        ref.setPadding(dp(10), dp(6), dp(10), dp(6));
        ref.setOnClickListener(v -> refresh());
        TextView out = tv("Çıkış", 14, 0xCCFFFFFF, false);
        out.setPadding(dp(10), dp(6), dp(4), dp(6));
        out.setOnClickListener(v -> new AlertDialog.Builder(this).setTitle("Çıkış yap")
                .setMessage("Kayıtlı token silinsin ve bildirimler durdurulsun mu?")
                .setPositiveButton("Çıkış", (d, w) -> logout()).setNegativeButton("Vazgeç", null).show());
        row.addView(ref);
        row.addView(out);
        header.addView(row);
        status = tv("", 13, 0xCCFFFFFF, false);
        header.addView(status, lp(-2, -2, 0, 2));
        root.addView(header);

        LinearLayout tabs = new LinearLayout(this);
        tabs.setBackgroundColor(0xFFFFFFFF);
        tabs.setElevation(dp(3));
        String[] names = {"Teslimler", "Duyurular", "Notlar"};
        tabViews = new TextView[3];
        for (int i = 0; i < 3; i++) {
            final int idx = i;
            TextView t = tv(names[i], 15, MUTED, true);
            t.setGravity(Gravity.CENTER);
            t.setPadding(0, dp(13), 0, dp(13));
            t.setOnClickListener(v -> {
                tab = idx;
                render();
            });
            tabViews[i] = t;
            tabs.addView(t, new LinearLayout.LayoutParams(0, -2, 1));
        }
        root.addView(tabs);

        ScrollView sv = new ScrollView(this);
        list = box(12);
        sv.addView(list);
        root.addView(sv, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);

        data = Store.cache(this);
        render();
        SyncJob.schedule(this);
        refresh();
    }

    void logout() {
        Store.logout(this);
        getSystemService(JobScheduler.class).cancelAll();
        getSystemService(NotificationManager.class).cancelAll();
        data = null;
        showLogin();
    }

    void refresh() {
        if (refreshing) return;
        refreshing = true;
        status.setText("Güncelleniyor…");
        io.execute(() -> {
            String msg;
            try {
                JSONObject d = Sync.run(getApplicationContext());
                data = d;
                lastRefresh = System.currentTimeMillis();
                int errs = d.getJSONArray("errors").length();
                msg = "Son güncelleme: " + new java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(new java.util.Date())
                        + (errs > 0 ? " · " + errs + " kaynak okunamadı" : "");
            } catch (Canvas.ApiException e) {
                msg = e.code == 401 ? "Token geçersiz/süresi dolmuş — Çıkış yapıp yenisini gir." : "Canvas hatası (HTTP " + e.code + ")";
            } catch (Exception e) {
                msg = data != null ? "Bağlantı yok — son kaydedilen veriler gösteriliyor." : "Bağlantı kurulamadı.";
            }
            final String m = msg;
            ui.post(() -> {
                refreshing = false;
                if (!mainShown) return;
                status.setText(m);
                render();
            });
        });
    }

    // ------------------------------------------------------------------ listeler
    void render() {
        for (int i = 0; i < 3; i++) {
            tabViews[i].setTextColor(i == tab ? BLUE : MUTED);
            tabViews[i].setBackground(i == tab ? underline() : null);
        }
        list.removeAllViews();
        if (data == null) {
            list.addView(tv("Veriler yükleniyor…", 15, MUTED, false));
            return;
        }
        try {
            if (tab == 0) renderDue(); else if (tab == 1) renderNews(); else renderGrades();
        } catch (Exception e) {
            list.addView(tv("Veriler gösterilemedi.", 15, RED, false));
        }
        if (list.getChildCount() == 0) {
            TextView t = tv("Gösterilecek bir şey yok.", 15, MUTED, false);
            t.setGravity(Gravity.CENTER);
            t.setPadding(0, dp(40), 0, 0);
            list.addView(t);
        }
    }

    android.graphics.drawable.Drawable underline() {
        GradientDrawable fill = new GradientDrawable();
        fill.setColor(0xFFFFFFFF);
        GradientDrawable bar = new GradientDrawable();
        bar.setColor(BLUE);
        android.graphics.drawable.LayerDrawable l = new android.graphics.drawable.LayerDrawable(new android.graphics.drawable.Drawable[]{fill, bar});
        l.setLayerGravity(1, Gravity.BOTTOM);
        l.setLayerHeight(1, dp(3));
        return l;
    }

    void renderDue() throws Exception {
        Instant now = Instant.now();
        List<JSONObject> items = new ArrayList<>();
        JSONArray as = data.getJSONArray("assignments");
        for (int i = 0; i < as.length(); i++) {
            JSONObject a = as.getJSONObject(i);
            if (a.isNull("due_at") || !Sync.needsSubmission(a)) continue;
            Instant due = Instant.parse(a.getString("due_at"));
            if (due.isAfter(now)) items.add(a);
        }
        Collections.sort(items, (x, y) -> x.optString("due_at").compareTo(y.optString("due_at")));
        for (JSONObject a : items) {
            boolean done = Sync.isDone(a.optJSONObject("submission"));
            Duration left = Duration.between(now, Instant.parse(a.getString("due_at")));
            int color = done ? GREEN : left.toHours() < 24 ? RED : left.toHours() < 72 ? ORANGE : BLUE;
            list.addView(card(a.optString("_course"), a.optString("name"), Fmt.date(a.getString("due_at")),
                    done ? "Teslim edildi ✓" : Fmt.left(left) + " kaldı", color, a.optString("html_url")));
        }
    }

    void renderNews() throws Exception {
        List<JSONObject> items = new ArrayList<>();
        JSONArray an = data.getJSONArray("announcements");
        for (int i = 0; i < an.length(); i++) items.add(an.getJSONObject(i));
        Collections.sort(items, (x, y) -> y.optString("posted_at").compareTo(x.optString("posted_at")));
        for (JSONObject a : items.subList(0, Math.min(40, items.size()))) {
            String body = Html.fromHtml(a.optString("message"), Html.FROM_HTML_MODE_COMPACT).toString().replaceAll("\\s+", " ").trim();
            if (body.length() > 160) body = body.substring(0, 160) + "…";
            View c = card(a.optString("_course"), a.optString("title"), Fmt.date(a.optString("posted_at")), null, 0, a.optString("html_url"));
            if (!body.isEmpty()) ((LinearLayout) c).addView(tv(body, 13, TEXT, false), lp(-2, -2, 0, 6));
            list.addView(c);
        }
    }

    void renderGrades() throws Exception {
        JSONArray cs = data.getJSONArray("courses");
        for (int i = 0; i < cs.length(); i++) {
            JSONObject c = cs.getJSONObject(i);
            if (!c.has("score")) continue;
            String g = "%" + Fmt.num(c.getDouble("score")) + (c.optString("grade").isEmpty() ? "" : " · " + c.optString("grade"));
            list.addView(card("Ders genel notu", c.getString("name"), null, g, BLUE, null));
        }
        List<JSONObject> graded = new ArrayList<>();
        JSONArray as = data.getJSONArray("assignments");
        for (int i = 0; i < as.length(); i++) {
            JSONObject s = as.getJSONObject(i).optJSONObject("submission");
            if (s != null && !s.isNull("score")) graded.add(as.getJSONObject(i));
        }
        Collections.sort(graded, (x, y) -> y.optJSONObject("submission").optString("graded_at")
                .compareTo(x.optJSONObject("submission").optString("graded_at")));
        if (!graded.isEmpty()) {
            TextView h = tv("Son notlar", 14, MUTED, true);
            h.setPadding(dp(4), dp(10), 0, dp(6));
            list.addView(h);
        }
        for (JSONObject a : graded.subList(0, Math.min(15, graded.size()))) {
            JSONObject s = a.getJSONObject("submission");
            String pts = Fmt.num(s.getDouble("score")) + (a.isNull("points_possible") ? "" : " / " + Fmt.num(a.getDouble("points_possible")));
            list.addView(card(a.optString("_course"), a.optString("name"), null, pts, GREEN, a.optString("html_url")));
        }
    }

    View card(String top, String title, String sub, String chip, int chipColor, String url) {
        LinearLayout c = cardBox();
        c.setLayoutParams(lp(-1, -2, 0, 0));
        ((LinearLayout.LayoutParams) c.getLayoutParams()).bottomMargin = dp(10);
        c.addView(tv(top, 12, BLUE, true));
        c.addView(tv(title, 16, TEXT, true), lp(-2, -2, 0, 3));
        if (sub != null) c.addView(tv(sub, 13, MUTED, false), lp(-2, -2, 0, 3));
        if (chip != null) {
            TextView ch = tv(chip, 12, 0xFFFFFFFF, true);
            ch.setBackground(round(chipColor, 12));
            ch.setPadding(dp(10), dp(4), dp(10), dp(4));
            c.addView(ch, lp(-2, -2, 0, 8));
        }
        if (url != null && url.startsWith("https://"))
            c.setOnClickListener(v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
        return c;
    }
}
