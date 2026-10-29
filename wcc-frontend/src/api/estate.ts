import { api } from './client';

export interface EstateClearResponse {
  jobs_removed: number;
  jobs_failed: number;
  archive_path: string;
}

// confirmJobCount must equal the estate's actual current job count -- the
// server refuses (409) otherwise. Pass the count this page already has from
// its own job list rather than fetching a second one just to confirm the
// first: that would just be two numbers that could disagree with each
// other, not an extra safety check.
export const clearEstate = (confirmJobCount: number): Promise<EstateClearResponse> =>
  api
    .delete('/estate', { params: { confirm_job_count: confirmJobCount } })
    .then((r: { data: EstateClearResponse }) => r.data);
