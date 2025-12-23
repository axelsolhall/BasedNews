package com.basednews.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class StatusService {
    private final ObjectMapper mapper;
    private final Path dataDir;

    public StatusService(ObjectMapper mapper, @Value("${basednews.data-dir:../data}") String dataDir) {
        this.mapper = mapper;
        this.dataDir = Paths.get(dataDir).toAbsolutePath().normalize();
    }

    public StatusResponse getStatus() {
        int countryCount = 0;
        int outletCount = 0;
        String latestIngestDate = null;
        long articlesLatestRun = 0;
        List<OutletCount> outletsLatestRun = new ArrayList<>();
        String error = null;

        try {
            Path outletsPath = dataDir.resolve("outlets.json");
            if (Files.exists(outletsPath)) {
                JsonNode root = mapper.readTree(outletsPath.toFile());
                JsonNode countries = root.path("countries");
                if (countries.isObject()) {
                    countryCount = countries.size();
                    Iterator<Map.Entry<String, JsonNode>> fields = countries.fields();
                    while (fields.hasNext()) {
                        Map.Entry<String, JsonNode> entry = fields.next();
                        JsonNode list = entry.getValue();
                        if (list.isArray()) {
                            outletCount += list.size();
                        }
                    }
                }
            }

            Path rawDir = dataDir.resolve("raw");
            latestIngestDate = findLatestRun(rawDir);
            if (latestIngestDate != null) {
                Path latestDir = rawDir.resolve(latestIngestDate);
                if (Files.isDirectory(latestDir)) {
                    try (DirectoryStream<Path> stream = Files.newDirectoryStream(latestDir, "*.jsonl")) {
                        for (Path file : stream) {
                            long count = countLines(file);
                            String outletId = stripExtension(file.getFileName().toString());
                            outletsLatestRun.add(new OutletCount(outletId, count));
                            articlesLatestRun += count;
                        }
                    }
                }
            }
        } catch (Exception exc) {
            error = exc.getMessage();
        }

        outletsLatestRun.sort(Comparator.comparing(OutletCount::outletId));
        return new StatusResponse(
                countryCount,
                outletCount,
                latestIngestDate,
                articlesLatestRun,
                outletsLatestRun,
                dataDir.toString(),
                error
        );
    }

    private static String findLatestRun(Path rawDir) throws IOException {
        if (!Files.isDirectory(rawDir)) {
            return null;
        }
        try (Stream<Path> stream = Files.list(rawDir)) {
            return stream
                    .filter(Files::isDirectory)
                    .map(path -> path.getFileName().toString())
                    .filter(name -> name.matches("\\d{8}"))
                    .max(String::compareTo)
                    .orElse(null);
        }
    }

    private static long countLines(Path path) throws IOException {
        try (Stream<String> lines = Files.lines(path)) {
            return lines.count();
        }
    }

    private static String stripExtension(String filename) {
        int dot = filename.lastIndexOf('.');
        if (dot == -1) {
            return filename;
        }
        return filename.substring(0, dot);
    }
}
