package com.basednews.outlets;

import java.util.List;

public record CountryOutletsDto(
    String country,
    List<OutletDto> outlets
) {}
