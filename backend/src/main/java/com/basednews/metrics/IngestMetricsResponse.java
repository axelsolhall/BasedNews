package com.basednews.metrics;

import java.util.List;

public record IngestMetricsResponse(
        List<String> days,
        List<IngestSeries> series
) {}
