package com.basednews.outlets;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.CrossOrigin;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "http://localhost:4200")
public class OutletsController {
    private final OutletsService service;

    public OutletsController(OutletsService service) {
        this.service = service;
    }

    @GetMapping("/outlets")
    public OutletsResponse getOutlets() {
        return service.loadOutlets();
    }
}
