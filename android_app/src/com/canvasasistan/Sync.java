package com.canvasasistan;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.Duration;
import java.time.Instant;

/** Veriyi çeker, önceki durumla karşılaştırır, yalnızca GERÇEK değişiklikler için bildirim gönderir. */
final class Sync {
    static final String CH_DEADLINE = "deadline", CH_NEWS = "news";

    static synchronized JSONObject run(Context c) throws Exception {
        Canvas api = new Canvas(Store.base(c), Store.token(c));
        JSONObject d = api.fetchAll();
        JSONObject st = Store.state(c);
        boolean baseline = !st.optBoolean("baselined", false);   // ilk çalışma: eski şeyler için bildirim yok
        JSONObject seenAnn = obj(st, "ann"), due = obj(st, "due"), score = obj(st, "score"), rem = obj(st, "rem");
        boolean pushOk = channels(c);

        JSONArray ans = d.getJSONArray("announcements");
        for (int i = 0; i < ans.length(); i++) {
            JSONObject a = ans.getJSONObject(i);
            String id = a.optString("id");
            if (!seenAnn.has(id)) {
                seenAnn.put(id, 1);
                if (!baseline && pushOk)
                    notify(c, CH_NEWS, "ann" + id, a.optString("_course") + " · Yeni duyuru", a.optString("title"),
                            a.optString("html_url"));
            }
        }

        Instant now = Instant.now();
        JSONArray as = d.getJSONArray("assignments");
        for (int i = 0; i < as.length(); i++) {
            JSONObject a = as.getJSONObject(i);
            String id = a.optString("id"), name = a.optString("name"), course = a.optString("_course");
            String dueAt = a.isNull("due_at") ? "" : a.optString("due_at", "");
            String url = a.optString("html_url");
            JSONObject sub = a.optJSONObject("submission");

            if (!baseline && pushOk) {
                if (!due.has(id)) {
                    notify(c, CH_NEWS, "new" + id, course + " · Yeni ödev",
                            name + (dueAt.isEmpty() ? "" : " — teslim: " + Fmt.date(dueAt)), url);
                } else if (!due.optString(id).equals(dueAt)) {
                    notify(c, CH_DEADLINE, "chg" + id + dueAt, course + " · Teslim tarihi değişti",
                            name + ": " + (dueAt.isEmpty() ? "tarih kaldırıldı" : Fmt.date(dueAt)), url);
                }
                if (sub != null && !sub.isNull("score")) {
                    String s = String.valueOf(sub.optDouble("score"));
                    if (!score.has(id) || !score.optString(id).equals(s))
                        notify(c, CH_NEWS, "gr" + id + s, course + (score.has(id) ? " · Not değişti" : " · Yeni not"),
                                name + ": " + Fmt.num(sub.optDouble("score")) +
                                        (a.isNull("points_possible") ? "" : " / " + Fmt.num(a.optDouble("points_possible"))), url);
                }
            }
            due.put(id, dueAt);
            if (sub != null && !sub.isNull("score")) score.put(id, String.valueOf(sub.optDouble("score")));

            // Yaklaşan teslim hatırlatması (24 sa ve 3 sa kala), teslim edilmediyse
            if (!dueAt.isEmpty() && pushOk && needsSubmission(a)) {
                long hours;
                try {
                    hours = Duration.between(now, Instant.parse(dueAt)).toHours();
                } catch (Exception e) {
                    continue;
                }
                Duration left = Duration.between(now, Instant.parse(dueAt));
                if (!left.isNegative() && !isDone(sub)) {
                    String k3 = id + ":" + dueAt + ":3", k24 = id + ":" + dueAt + ":24";
                    if (hours < 3 && !rem.has(k3)) {
                        rem.put(k3, 1);
                        rem.put(k24, 1);
                        notify(c, CH_DEADLINE, "r" + k3, "⏰ " + Fmt.left(left) + " kaldı", course + " · " + name, url);
                    } else if (hours < 24 && !rem.has(k24)) {
                        rem.put(k24, 1);
                        notify(c, CH_DEADLINE, "r" + k24, "Yarın teslim: " + name, course + " · " + Fmt.date(dueAt), url);
                    }
                }
            }
        }
        st.put("baselined", true).put("ann", seenAnn).put("due", due).put("score", score).put("rem", rem);
        Store.saveState(c, st);
        Store.saveCache(c, d);
        return d;
    }

    static boolean isDone(JSONObject sub) {
        if (sub == null) return false;
        String w = sub.optString("workflow_state", "");
        return w.equals("submitted") || w.equals("graded") || w.equals("pending_review") || sub.optBoolean("excused", false);
    }

    static boolean needsSubmission(JSONObject a) {
        JSONArray t = a.optJSONArray("submission_types");
        if (t == null) return true;
        for (int i = 0; i < t.length(); i++) {
            String s = t.optString(i);
            if (s.equals("none") || s.equals("not_graded") || s.equals("on_paper")) return false;
        }
        return true;
    }

    private static JSONObject obj(JSONObject s, String k) {
        JSONObject o = s.optJSONObject(k);
        return o == null ? new JSONObject() : o;
    }

    private static boolean channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CH_DEADLINE, "Teslim hatırlatmaları", NotificationManager.IMPORTANCE_HIGH));
        nm.createNotificationChannel(new NotificationChannel(CH_NEWS, "Duyurular ve notlar", NotificationManager.IMPORTANCE_DEFAULT));
        return nm.areNotificationsEnabled();
    }

    private static void notify(Context c, String ch, String key, String title, String text, String url) {
        Intent i = (url != null && url.startsWith("https://")) ? new Intent(Intent.ACTION_VIEW, Uri.parse(url))
                : new Intent(c, Main.class);
        PendingIntent pi = PendingIntent.getActivity(c, key.hashCode(), i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(c, ch)
                .setSmallIcon(R.drawable.ic_stat).setContentTitle(title).setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text)).setContentIntent(pi).setAutoCancel(true).build();
        c.getSystemService(NotificationManager.class).notify(key.hashCode(), n);
    }
}
