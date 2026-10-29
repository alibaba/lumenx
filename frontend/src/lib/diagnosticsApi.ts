import axios from "axios";
import { API_URL } from "@/lib/apiUrl";

/** Shared read-only service diagnostics, independent of either product. */
export const diagnosticsApi = {
    healthCheck: async (): Promise<{
        ok: boolean;
        time: number;
        log_file: string;
        log_dir: string;
        studio_projects: number;
    }> => {
        const res = await axios.get(`${API_URL}/health`, { timeout: 5000 });
        return res.data;
    },

    diagnoseLogTail: async (lines: number = 200): Promise<{
        path: string;
        total_lines?: number;
        returned_lines?: number;
        lines: string[];
        errors: string[];
        missing: boolean;
    }> => {
        const res = await axios.get(`${API_URL}/diagnose/log_tail`, {
            params: { lines },
            timeout: 8000,
        });
        return res.data;
    },
};
