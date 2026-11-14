package com.canvasasistan;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Salt-okunur Canvas istemcisi: yalnızca GET. Hiçbir şey göndermez/teslim etmez. */
final class Canvas {
    static final class ApiException extends IOException {
        final int code;
        ApiException(int code) {
            super("HTTP " + code);
            this.code = code;
        }
    }

    private static final Pattern NEXT = Pattern.compile("<([^>]+)>;\\s*rel=\"next\"");
    private final String base, token;

    Canvas(String base, String token) {
        if (!base.startsWith("https://")) throw new IllegalArgumentException("Adres https:// ile başlamalı");
        this.base = base.replaceAll("/+$", "");
        this.token = token;
    }

    private String[] get(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(30000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Accept", "application/json");
        int code = c.getResponseCode();
        if (code != 200) throw new ApiException(code);
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream o = new ByteArrayOutputStream();
            byte[] b = new byte[8192];
            int n;
            while ((n = in.read(b)) > 0) o.write(b, 0, n);
            String link = c.getHeaderField("Link");
            return new String[]{o.toString(StandardCharsets.UTF_8.name()), link == null ? "" : link};
        }
    }

    JSONObject object(String path) throws Exception {
        return new JSONObject(get(base + path)[0]);
    }

    JSONArray list(String path) throws Exception {
        JSONArray all = new JSONArray();
        String url = base + path;
        for (int page = 0; page < 10 && url != null; page++) {
            String[] r = get(url);
            JSONArray a = new JSONArray(r[0]);
            for (int i = 0; i < a.length(); i++) all.put(a.get(i));
            Matcher m = NEXT.matcher(r[1]);
            url = m.find() ? m.group(1) : null;
        }
        return all;
    }

    /** Dersler + ödevler + duyurular. Okunamayan kaynaklar "errors" içinde raporlanır, uydurulmaz. */
    JSONObject fetchAll() throws Exception {
        JSONObject out = new JSONObject();
        JSONArray courses = new JSONArray(), assignments = new JSONArray(), announcements = new JSONArray(),
                errors = new JSONArray();
        JSONObject names = new JSONObject();
        JSONArray raw = list("/api/v1/courses?enrollment_state=active&include%5B%5D=total_scores&per_page=100");
        StringBuilder ctx = new StringBuilder();
        int inBatch = 0;
        JSONArray batches = new JSONArray();
        for (int i = 0; i < raw.length(); i++) {
            JSONObject c = raw.getJSONObject(i);
            if (!c.has("id") || c.optBoolean("access_restricted_by_date", false)) continue;
            String id = String.valueOf(c.getLong("id"));
            String name = c.optString("name", c.optString("course_code", "Ders " + id));
            names.put("course_" + id, name);
            JSONObject cc = new JSONObject().put("id", id).put("name", name);
            JSONArray enr = c.optJSONArray("enrollments");
            if (enr != null) for (int k = 0; k < enr.length(); k++) {
                JSONObject e = enr.getJSONObject(k);
                if ("student".equals(e.optString("type")) && !e.isNull("computed_current_score")) {
                    cc.put("score", e.getDouble("computed_current_score"));
                    cc.put("grade", e.optString("computed_current_grade", ""));
                }
            }
            courses.put(cc);
            try {
                JSONArray as = list("/api/v1/courses/" + id + "/assignments?include%5B%5D=submission&order_by=due_at&per_page=100");
                for (int k = 0; k < as.length(); k++) {
                    JSONObject a = as.getJSONObject(k);
                    a.remove("description");   // büyük HTML'i saklamayalım
                    a.put("_course", name);
                    assignments.put(a);
                }
            } catch (ApiException e) {
                errors.put(name + ": ödevler okunamadı (HTTP " + e.code + ")");
            }
            ctx.append("&context_codes%5B%5D=course_").append(id);
            if (++inBatch == 10) {
                batches.put(ctx.toString());
                ctx.setLength(0);
                inBatch = 0;
            }
        }
        if (inBatch > 0) batches.put(ctx.toString());
        String start = Instant.now().minus(60, ChronoUnit.DAYS).toString().substring(0, 10);
        for (int b = 0; b < batches.length(); b++) {
            try {
                JSONArray an = list("/api/v1/announcements?start_date=" + start + "&per_page=50" + batches.getString(b));
                for (int k = 0; k < an.length(); k++) {
                    JSONObject a = an.getJSONObject(k);
                    a.put("_course", names.optString(a.optString("context_code"), "Ders"));
                    announcements.put(a);
                }
            } catch (ApiException e) {
                errors.put("Duyurular okunamadı (HTTP " + e.code + ")");
            }
        }
        return out.put("courses", courses).put("assignments", assignments).put("announcements", announcements)
                .put("errors", errors).put("time", System.currentTimeMillis());
    }
}
