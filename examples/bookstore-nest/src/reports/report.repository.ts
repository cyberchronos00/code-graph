/** Read model for sales reports (bound to an implementation in ReportsModule). */
export interface ReportRepository {
  topSellers(store: string, from?: string): Promise<unknown[]>;
  remove(id: number): Promise<void>;
}

export const REPORT_REPOSITORY = 'REPORT_REPOSITORY';
