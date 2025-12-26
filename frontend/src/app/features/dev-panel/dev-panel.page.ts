import { Component, OnInit } from '@angular/core';
import { OutletsApiService } from '../../core/api/outlets-api.service';
import { IngestMetricsApiService } from '../../core/api/ingest-metrics-api.service';
import { CountryOutlets, IngestSeries } from '../../core/models';

type DevTab = 'ingestion' | 'matching';

@Component({
  selector: 'app-dev-panel-page',
  templateUrl: './dev-panel.page.html',
  styleUrls: ['./dev-panel.page.css']
})
export class DevPanelPageComponent implements OnInit {
  activeTab: 'ingestion' | 'matching' = 'ingestion';
  searchQuery = '';
  countries: CountryOutlets[] = [];
  ingestDays: string[] = [];
  private seriesByKey = new Map<string, IngestSeries>();
  private countryCodeMap: Record<string, string> = {
    Norway: 'no',
    Sweden: 'se',
    Denmark: 'dk'
  };

  constructor(
    private outletsApi: OutletsApiService,
    private ingestMetricsApi: IngestMetricsApiService
  ) {}

  ngOnInit(): void {
    this.outletsApi.getOutlets().subscribe((res) => {
      this.countries = res.countries;
    });
    this.ingestMetricsApi.getIngestMetrics(7).subscribe((res) => {
      this.ingestDays = res.days;
      this.seriesByKey.clear();
      for (const series of res.series) {
        this.seriesByKey.set(this.seriesKey(series.country, series.outletId), series);
      }
    });
  }

  setTab(tab: 'ingestion' | 'matching'): void {
    this.activeTab = tab;
  }

  onSearchChange(value: string): void {
    this.searchQuery = value;
  }

  flagClass(countryName: string): string {
    const code = this.countryCodeMap[countryName];
    return code ? `flag-${code}` : 'flag-unknown';
  }

  outletSeries(country: string, outletId: string): IngestSeries | undefined {
    return this.seriesByKey.get(this.seriesKey(country, outletId));
  }

  sparklinePoints(values: number[], width = 90, height = 24): string {
    if (!values || values.length === 0) {
      return '';
    }
    const max = Math.max(...values, 1);
    const step = values.length > 1 ? width / (values.length - 1) : width;
    return values
      .map((v, i) => `${i * step},${height - (v / max) * height}`)
      .join(' ');
  }

  private seriesKey(country: string, outletId: string): string {
    return `${country}|${outletId}`;
  }
}
