export interface IngestionPoint {
  date: string;
  outletId: string;
  count: number;
}

export interface IngestionSeries {
  country: string;
  points: IngestionPoint[];
}
