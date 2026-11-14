package com.canvasasistan;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

final class Fmt {
    private static final DateTimeFormatter F =
            DateTimeFormatter.ofPattern("d MMMM EEEE, HH:mm", new Locale("tr")).withZone(ZoneId.systemDefault());
    private static final DateTimeFormatter SHORT =
            DateTimeFormatter.ofPattern("d MMM, HH:mm", new Locale("tr")).withZone(ZoneId.systemDefault());

    static String date(String iso) {
        try {
            return F.format(Instant.parse(iso));
        } catch (Exception e) {
            return iso;
        }
    }

    static String shortDate(String iso) {
        try {
            return SHORT.format(Instant.parse(iso));
        } catch (Exception e) {
            return iso;
        }
    }

    static String num(double d) {
        return d == Math.rint(d) ? String.valueOf((long) d) : String.format(Locale.US, "%.1f", d);
    }

    static String left(Duration d) {
        long m = Math.max(0, d.toMinutes());
        if (m >= 2 * 24 * 60) return (m / (24 * 60)) + " gün";
        if (m >= 24 * 60) return "1 gün " + ((m % (24 * 60)) / 60) + " sa";
        if (m >= 60) return (m / 60) + " sa " + (m % 60) + " dk";
        return m + " dk";
    }
}
