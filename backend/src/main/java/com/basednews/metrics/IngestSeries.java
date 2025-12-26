package com.basednews.metrics;

import java.util.List;

public record IngestSeries(
        String country,
        String outletId,
        String outletName,
        List<Integer> counts
) {}
