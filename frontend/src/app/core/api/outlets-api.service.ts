import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { OutletsResponse } from '../models';

@Injectable({ providedIn: 'root' })
export class OutletsApiService {
  constructor(private http: HttpClient) {}

  getOutlets(): Observable<OutletsResponse> {
    return this.http.get<OutletsResponse>(`${environment.apiBaseUrl}/outlets`);
  }
}
