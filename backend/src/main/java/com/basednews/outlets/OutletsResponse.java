package com.basednews.outlets;

import java.util.List;

public record OutletsResponse(
    List<CountryOutletsDto> countries
) {}
