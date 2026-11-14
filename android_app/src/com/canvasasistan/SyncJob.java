package com.canvasasistan;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

/** Uygulama kapalıyken bile ~15 dk'da bir Canvas'ı kontrol eder (Android'in izin verdiği en sık aralık). */
public class SyncJob extends JobService {
    static void schedule(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js.getPendingJob(1) != null) return;
        js.schedule(new JobInfo.Builder(1, new ComponentName(c, SyncJob.class))
                .setPeriodic(15 * 60 * 1000L)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build());
    }

    @Override
    public boolean onStartJob(JobParameters p) {
        if (!Store.loggedIn(this)) return false;
        new Thread(() -> {
            boolean retry = false;
            try {
                Sync.run(getApplicationContext());
            } catch (Exception e) {
                retry = true;   // ağ yok / geçici hata: Android daha sonra tekrar dener
            }
            jobFinished(p, retry);
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters p) {
        return true;
    }
}
