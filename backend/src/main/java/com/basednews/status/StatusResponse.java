package com.basednews.status;

import java.util.List;

public record StatusResponse(
        int countryCount,
        int outletCount,
        String latestIngestDate,
        long articlesLatestRun,
        List<OutletCount> outletsLatestRun,
        String dataDir,
        String error
) {}
