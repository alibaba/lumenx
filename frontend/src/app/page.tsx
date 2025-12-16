"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Plus, FolderOpen, Settings } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import ProjectCard from "@/components/project/ProjectCard";
import CreateProjectDialog from "@/components/project/CreateProjectDialog";
import EnvConfigDialog from "@/components/project/EnvConfigDialog";
import CreativeCanvas from "@/components/canvas/CreativeCanvas";
import dynamic from "next/dynamic";
import { api } from "@/lib/api";

const ProjectClient = dynamic(() => import("@/app/project/[id]/ProjectClient"), { ssr: false });

export default function Home() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isEnvDialogOpen, setIsEnvDialogOpen] = useState(false);
  const [envRequired, setEnvRequired] = useState(false);
  const [currentView, setCurrentView] = useState<'home' | 'project'>('home');
  const [projectId, setProjectId] = useState<string | null>(null);
  const projects = useProjectStore((state) => state.projects);
  const deleteProject = useProjectStore((state) => state.deleteProject);

  // Check environment variables on startup
  useEffect(() => {
    checkEnvConfig();
  }, []);

  const checkEnvConfig = async () => {
    try {
      const config = await api.getEnvConfig();
      // 空值和空字符串都视为未配置
      const dashscopeKey = config.DASHSCOPE_API_KEY?.trim();
      const accessKeyId = config.ALIBABA_CLOUD_ACCESS_KEY_ID?.trim();
      const accessKeySecret = config.ALIBABA_CLOUD_ACCESS_KEY_SECRET?.trim();
      
      const hasRequired = dashscopeKey && dashscopeKey.length > 0 &&
                         accessKeyId && accessKeyId.length > 0 &&
                         accessKeySecret && accessKeySecret.length > 0;
      
      if (!hasRequired) {
        setEnvRequired(true);
        setIsEnvDialogOpen(true);
      }
    } catch (error) {
      console.error("Failed to check env config:", error);
    }
  };

  // 监听 hash 变化
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash;
      if (hash.startsWith('#/project/')) {
        const id = hash.replace('#/project/', '');
        setProjectId(id);
        setCurrentView('project');
      } else {
        setCurrentView('home');
        setProjectId(null);
      }
    };

    // 初始化时检查 hash
    handleHashChange();

    // 监听 hash 变化
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  // 如果是项目详情页，渲染项目详情组件
  if (currentView === 'project' && projectId) {
    return <ProjectClient params={{ id: projectId }} />;
  }

  return (
    <main className="relative min-h-screen w-screen bg-background overflow-auto">
      {/* Background Canvas */}
      <div className="fixed inset-0 z-0 pointer-events-none">
        <CreativeCanvas />
      </div>

      {/* Main Content */}
      <div className="relative z-10 container mx-auto px-6 py-12">
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-12"
        >
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl md:text-5xl font-display font-bold text-white mb-4">
                云创 AI 漫剧工作室
              </h1>
              <p className="text-gray-400 text-lg">
                创建和管理您的AI动漫项目
              </p>
            </div>
            <button
              onClick={() => {
                setEnvRequired(false);
                setIsEnvDialogOpen(true);
              }}
              className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors"
              title="配置环境变量"
            >
              <Settings size={20} />
              配置
            </button>
          </div>
        </motion.div>

        {/* Projects Grid */}
        {projects.length === 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col items-center justify-center py-20"
          >
            <FolderOpen size={64} className="text-gray-600 mb-4" />
            <h3 className="text-xl font-medium text-gray-400 mb-2">还没有项目</h3>
            <p className="text-gray-500 mb-6">创建第一个项目开始吧！</p>
            <button
              onClick={() => setIsDialogOpen(true)}
              className="bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium flex items-center gap-2 transition-colors"
            >
              <Plus size={20} />
              创建新项目
            </button>
          </motion.div>
        ) : (
          <>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-display font-bold text-white">
                我的项目 ({projects.length})
              </h2>
              <button
                onClick={() => setIsDialogOpen(true)}
                className="bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
              >
                <Plus size={16} />
                新建项目
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {projects.map((project, i) => (
                <motion.div
                  key={project.id}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.1 }}
                >
                  <ProjectCard project={project} onDelete={deleteProject} />
                </motion.div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Create Project Dialog */}
      <CreateProjectDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
      />

      {/* Environment Configuration Dialog */}
      <EnvConfigDialog
        isOpen={isEnvDialogOpen}
        onClose={() => {
          setIsEnvDialogOpen(false);
          setEnvRequired(false);
        }}
        isRequired={envRequired}
      />
    </main>
  );
}
