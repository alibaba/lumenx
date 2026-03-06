"use client";

import { useState, useEffect } from "react";
import { X, Save, Settings } from "lucide-react";
import { api } from "@/lib/api";

interface EnvConfigDialogProps {
  isOpen: boolean;
  onClose: () => void;
  isRequired?: boolean;
}

interface EnvConfig {
  DASHSCOPE_API_KEY: string;
  ALIBABA_CLOUD_ACCESS_KEY_ID: string;
  ALIBABA_CLOUD_ACCESS_KEY_SECRET: string;
  OSS_BUCKET_NAME: string;
  OSS_ENDPOINT: string;
  OSS_BASE_PATH: string;
  [key: string]: string; // 添加索引签名以兼容Record类型
}

export default function EnvConfigDialog({ isOpen, onClose, isRequired = false }: EnvConfigDialogProps) {
  const [config, setConfig] = useState<EnvConfig>({
    DASHSCOPE_API_KEY: "",
    ALIBABA_CLOUD_ACCESS_KEY_ID: "",
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: "",
    OSS_BUCKET_NAME: "",
    OSS_ENDPOINT: "",
    OSS_BASE_PATH: "",
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadConfig();
    }
  }, [isOpen]);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const data = await api.getEnvConfig();
      setConfig(data);
    } catch (error) {
      console.error("Failed to load env config:", error);
    } finally {
      setLoading(false);
    }
  };

  const validateRequiredFields = () => {
    const dashscopeKey = config.DASHSCOPE_API_KEY?.trim();
    const accessKeyId = config.ALIBABA_CLOUD_ACCESS_KEY_ID?.trim();
    const accessKeySecret = config.ALIBABA_CLOUD_ACCESS_KEY_SECRET?.trim();
    const ossBucket = config.OSS_BUCKET_NAME?.trim();
    const ossEndpoint = config.OSS_ENDPOINT?.trim();

    return dashscopeKey && dashscopeKey.length > 0 &&
      accessKeyId && accessKeyId.length > 0 &&
      accessKeySecret && accessKeySecret.length > 0 &&
      ossBucket && ossBucket.length > 0 &&
      ossEndpoint && ossEndpoint.length > 0;
  };

  const handleSave = async () => {
    // 必填项校验：空值和空字符串都视为未填写
    if (!validateRequiredFields()) {
      alert("请填写所有必填项：\n- DashScope API Key\n- 阿里云 Access Key ID\n- 阿里云 Access Key Secret\n- OSS Bucket Name\n- OSS Endpoint");
      return;
    }

    setSaving(true);
    try {
      await api.saveEnvConfig(config);
      alert("配置保存成功！");
      onClose();
      // 如果是必填配置保存成功后，可以考虑刷新页面以重新检查配置
      if (isRequired) {
        window.location.reload();
      }
    } catch (error) {
      console.error("Failed to save env config:", error);
      alert("保存配置失败,请重试");
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (key: keyof EnvConfig, value: string) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const canClose = !isRequired || validateRequiredFields();

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-800 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-800">
          <div className="flex items-center gap-3">
            <Settings className="text-primary" size={24} />
            <div>
              <h2 className="text-xl font-bold text-white">环境变量配置</h2>
              <p className="text-sm text-gray-400">配置阿里云服务的访问凭证</p>
            </div>
          </div>
          {canClose && (
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-white transition-colors"
            >
              <X size={20} />
            </button>
          )}
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {isRequired && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
              <p className="text-yellow-500 text-sm">
                ⚠️ 检测到环境变量缺失，请填写以下必填项以继续使用系统。
              </p>
            </div>
          )}

          {loading ? (
            <div className="text-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto"></div>
              <p className="text-gray-400 mt-4">加载配置中...</p>
            </div>
          ) : (
            <>
              {/* DashScope API Key */}
              <div>
                <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                  <span>DashScope API Key <span className="text-red-500">*</span></span>
                  <span className="text-gray-500 font-normal">例: sk-xxx</span>
                </label>
                <input
                  type="password"
                  value={config.DASHSCOPE_API_KEY}
                  onChange={(e) => handleChange("DASHSCOPE_API_KEY", e.target.value)}
                  placeholder="用于通义千问等模型"
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                />
              </div>

              {/* Alibaba Cloud Access Keys */}
              <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4 space-y-4">
                <p className="text-sm text-gray-400 mb-2">用于 OSS 存储服务</p>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    阿里云 Access Key ID <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="password"
                    value={config.ALIBABA_CLOUD_ACCESS_KEY_ID}
                    onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_ID", e.target.value)}
                    placeholder="LTAI5t..."
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    阿里云 Access Key Secret <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="password"
                    value={config.ALIBABA_CLOUD_ACCESS_KEY_SECRET}
                    onChange={(e) => handleChange("ALIBABA_CLOUD_ACCESS_KEY_SECRET", e.target.value)}
                    placeholder="阿里云访问密钥"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                  />
                </div>
              </div>

              {/* OSS Configuration */}
              <div className="pt-4 border-t border-gray-800">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-white">OSS 配置</h3>
                  <a
                    href="https://oss.console.aliyun.com/overview"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary hover:text-primary/80 transition-colors"
                  >
                    打开 OSS 控制台 →
                  </a>
                </div>

                <div className="space-y-4">
                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>OSS Bucket Name <span className="text-red-500">*</span></span>
                      <span className="text-gray-500 font-normal">例: my-comic-bucket</span>
                    </label>
                    <input
                      type="text"
                      value={config.OSS_BUCKET_NAME}
                      onChange={(e) => handleChange("OSS_BUCKET_NAME", e.target.value)}
                      placeholder="your_bucket_name"
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                    />
                  </div>

                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>OSS Endpoint <span className="text-red-500">*</span></span>
                      <span className="text-gray-500 font-normal">例: oss-cn-hangzhou.aliyuncs.com</span>
                    </label>
                    <input
                      type="text"
                      value={config.OSS_ENDPOINT}
                      onChange={(e) => handleChange("OSS_ENDPOINT", e.target.value)}
                      placeholder="oss-cn-beijing.aliyuncs.com"
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                    />
                  </div>

                  <div>
                    <label className="flex items-center justify-between text-sm font-medium text-gray-300 mb-2">
                      <span>OSS Base Path</span>
                      <span className="text-gray-500 font-normal">例: lumenx</span>
                    </label>
                    <input
                      type="text"
                      value={config.OSS_BASE_PATH}
                      onChange={(e) => handleChange("OSS_BASE_PATH", e.target.value)}
                      placeholder="lumenx"
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                    />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-800">
          {canClose && (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg transition-colors"
            >
              取消
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving || loading}
            className="px-4 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg font-medium flex items-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save size={16} />
            {saving ? "保存中..." : "保存配置"}
          </button>
        </div>
      </div>
    </div>
  );
}
