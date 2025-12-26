package com.basednews.metrics;

import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "http://localhost:4200")
public class IngestMetricsController {
    private final IngestMetricsService service;

    public IngestMetricsController(IngestMetricsService service) {
        this.service = service;
    }

    @GetMapping("/ingest-metrics")
    public IngestMetricsResponse getIngestMetrics(
            @RequestParam(value = "days", defaultValue = "7") int days,
            @RequestParam(value = "country", required = false) String country
    ) {
        return service.getMetrics(days, country);
    }
}
