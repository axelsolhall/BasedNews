package com.basednews.metrics;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class IngestMetricsService {
    private final ObjectMapper mapper;
    private final Path metricsPath;

    public IngestMetricsService(
            ObjectMapper mapper,
            @Value("${basednews.data-dir:../data}") String dataDir
    ) {
        this.mapper = mapper;
        Path base = Paths.get(dataDir).toAbsolutePath().normalize();
        this.metricsPath = base.resolve("metrics").resolve("ingest_counts.jsonl");
    }

    public IngestMetricsResponse getMetrics(int days, String countryFilter) {
        Path path = resolveMetricsPath();
        if (path == null || days <= 0) {
            return new IngestMetricsResponse(List.of(), List.of());
        }

        List<MetricRow> rows = new ArrayList<>();
        LocalDate maxDate = null;

        try (BufferedReader reader = Files.newBufferedReader(path)) {
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                JsonNode node = mapper.readTree(line);
                String country = textOrNull(node, "country");
                if (countryFilter != null && !countryFilter.isBlank()) {
                    if (country == null || !country.equalsIgnoreCase(countryFilter)) {
                        continue;
                    }
                }
                String outletId = textOrNull(node, "outlet_id");
                String outletName = textOrNull(node, "outlet_name");
                String retrievedAt = textOrNull(node, "retrieved_at");
                int count = node.path("count").asInt(0);
                if (country == null || outletId == null || retrievedAt == null) {
                    continue;
                }
                Instant instant;
                try {
                    instant = Instant.parse(retrievedAt);
                } catch (Exception ignored) {
                    continue;
                }
                LocalDate date = instant.atZone(ZoneOffset.UTC).toLocalDate();
                if (maxDate == null || date.isAfter(maxDate)) {
                    maxDate = date;
                }
                rows.add(new MetricRow(country, outletId, outletName, date, count));
            }
        } catch (Exception ignored) {
            return new IngestMetricsResponse(List.of(), List.of());
        }

        if (maxDate == null) {
            return new IngestMetricsResponse(List.of(), List.of());
        }

        List<LocalDate> dayList = new ArrayList<>();
        for (int i = days - 1; i >= 0; i--) {
            dayList.add(maxDate.minusDays(i));
        }
        List<String> dayStrings = dayList.stream().map(LocalDate::toString).toList();

        Map<String, Map<LocalDate, Integer>> countsByKey = new HashMap<>();
        Map<String, String> outletNames = new HashMap<>();

        for (MetricRow row : rows) {
            if (!dayList.contains(row.date())) {
                continue;
            }
            String key = row.country() + "|" + row.outletId();
            countsByKey
                    .computeIfAbsent(key, k -> new HashMap<>())
                    .merge(row.date(), row.count(), Integer::sum);
            if (row.outletName() != null) {
                outletNames.put(key, row.outletName());
            }
        }

        List<IngestSeries> series = new ArrayList<>();
        for (Map.Entry<String, Map<LocalDate, Integer>> entry : countsByKey.entrySet()) {
            String key = entry.getKey();
            String[] parts = key.split("\\|", 2);
            String country = parts[0];
            String outletId = parts.length > 1 ? parts[1] : "";
            List<Integer> counts = new ArrayList<>();
            Map<LocalDate, Integer> byDate = entry.getValue();
            for (LocalDate day : dayList) {
                counts.add(byDate.getOrDefault(day, 0));
            }
            series.add(new IngestSeries(country, outletId, outletNames.get(key), counts));
        }

        return new IngestMetricsResponse(dayStrings, series);
    }

    private Path resolveMetricsPath() {
        if (Files.exists(metricsPath)) {
            return metricsPath;
        }
        Path local = Paths.get("data", "metrics", "ingest_counts.jsonl").toAbsolutePath().normalize();
        if (Files.exists(local)) {
            return local;
        }
        Path parent = Paths.get("..", "data", "metrics", "ingest_counts.jsonl").toAbsolutePath().normalize();
        if (Files.exists(parent)) {
            return parent;
        }
        return null;
    }

    private static String textOrNull(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        String text = value.asText();
        return text.isBlank() ? null : text;
    }

    private record MetricRow(
            String country,
            String outletId,
            String outletName,
            LocalDate date,
            int count
    ) {}
}
