export interface IngestSeries {
  country: string;
  outletId: string;
  outletName?: string;
  counts: number[];
}

export interface IngestMetricsResponse {
  days: string[];
  series: IngestSeries[];
}
