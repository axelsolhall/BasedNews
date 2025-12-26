export interface Outlet {
  id: string;
  name: string;
  type: string;
  homepage: string;
  feeds: string[];
}

export interface CountryOutlets {
  country: string;
  outlets: Outlet[];
}

export interface OutletsResponse {
  countries: CountryOutlets[];
}
