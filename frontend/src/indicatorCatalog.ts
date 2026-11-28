import { api } from './api';

export type IndicatorPeriodBounds = {
  min: number;
  max: number;
};

export type CatalogParameters = {
  period?: Partial<IndicatorPeriodBounds>;
};

const DEFAULT_PERIOD_BOUNDS: IndicatorPeriodBounds = { min: 2, max: 200 };

let cachedCatalogPromise: Promise<unknown[]> | undefined;
let cachedCatalogItems: unknown[] | undefined;

export function resetIndicatorCatalogCache() {
  cachedCatalogPromise = undefined;
  cachedCatalogItems = undefined;
}

export async function loadIndicatorCatalog<T>(loader: () => Promise<T[]> = () => api.get<T[]>('/indicators')): Promise<T[]> {
  if (cachedCatalogItems) return cachedCatalogItems as T[];
  if (!cachedCatalogPromise) {
    cachedCatalogPromise = loader().then(items => {
      cachedCatalogItems = items;
      return items;
    }).catch(error => {
      cachedCatalogPromise = undefined;
      throw error;
    });
  }
  return (await cachedCatalogPromise) as T[];
}

export function periodBounds(definition?: { parameters?: CatalogParameters } | null): IndicatorPeriodBounds {
  const period = definition?.parameters?.period;
  const min = typeof period?.min === 'number' && Number.isInteger(period.min) && period.min >= 1
    ? period.min
    : DEFAULT_PERIOD_BOUNDS.min;
  const max = typeof period?.max === 'number' && Number.isInteger(period.max) && period.max >= min
    ? period.max
    : DEFAULT_PERIOD_BOUNDS.max;
  return { min, max };
}

export function isValidIndicatorPeriod(
  period: number | null,
  definition?: { parameters?: CatalogParameters } | null,
): boolean {
  if (period === null) return true;
  const { min, max } = periodBounds(definition);
  return Number.isInteger(period) && period >= min && period <= max;
}
