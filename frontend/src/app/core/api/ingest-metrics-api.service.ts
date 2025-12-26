import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { IngestMetricsResponse } from '../models';

@Injectable({ providedIn: 'root' })
export class IngestMetricsApiService {
  constructor(private http: HttpClient) {}

  getIngestMetrics(days = 7, country?: string): Observable<IngestMetricsResponse> {
    let params = new HttpParams().set('days', days);
    if (country) {
      params = params.set('country', country);
    }
    return this.http.get<IngestMetricsResponse>(`${environment.apiBaseUrl}/ingest-metrics`, { params });
  }
}
