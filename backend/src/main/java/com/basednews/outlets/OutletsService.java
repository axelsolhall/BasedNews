package com.basednews.outlets;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class OutletsService {
    private final ObjectMapper mapper;
    private final Path dataDir;

    public OutletsService(ObjectMapper mapper, @Value("${basednews.data-dir:../data}") String dataDir) {
        this.mapper = mapper;
        this.dataDir = Paths.get(dataDir).toAbsolutePath().normalize();
    }

    public OutletsResponse loadOutlets() {
        Path path = resolveDataPath("outlets.json");
        if (path == null) {
            return new OutletsResponse(List.of());
        }

        List<CountryOutletsDto> result = new ArrayList<>();
        try {
            JsonNode root = mapper.readTree(path.toFile());
            JsonNode countries = root.path("countries");
            Iterator<Map.Entry<String, JsonNode>> fields = countries.fields();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> entry = fields.next();
                String country = entry.getKey();
                JsonNode outletArray = entry.getValue();
                List<OutletDto> outlets = new ArrayList<>();
                for (JsonNode outletNode : outletArray) {
                    outlets.add(new OutletDto(
                            outletNode.path("id").asText(),
                            outletNode.path("name").asText(),
                            outletNode.path("type").asText(),
                            outletNode.path("homepage").asText(),
                            mapper.convertValue(outletNode.path("feeds"), List.class)
                    ));
                }
                result.add(new CountryOutletsDto(country, outlets));
            }
        } catch (Exception ignored) {
            return new OutletsResponse(List.of());
        }

        return new OutletsResponse(result);
    }

    private Path resolveDataPath(String filename) {
        Path candidate = dataDir.resolve(filename);
        if (Files.exists(candidate)) {
            return candidate;
        }
        Path local = Paths.get("data").resolve(filename).toAbsolutePath().normalize();
        if (Files.exists(local)) {
            return local;
        }
        Path parent = Paths.get("..", "data").resolve(filename).toAbsolutePath().normalize();
        if (Files.exists(parent)) {
            return parent;
        }
        return null;
    }
}
