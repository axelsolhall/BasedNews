package com.basednews.outlets;

import java.util.List;

public record OutletDto(
    String id,
    String name,
    String type,
    String homepage,
    List<String> feeds
) {}
