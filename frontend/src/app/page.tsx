"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Plus, FolderOpen, Settings,RefreshCw } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import ProjectCard from "@/components/project/ProjectCard";
import CreateProjectDialog from "@/components/project/CreateProjectDialog";
import EnvConfigDialog from "@/components/project/EnvConfigDialog";
import CreativeCanvas from "@/components/canvas/CreativeCanvas";
import dynamic from "next/dynamic";

const ProjectClient = dynamic(() => import("@/app/project/[id]/ProjectClient"), { ssr: false });

export default function Home() {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isEnvDialogOpen, setIsEnvDialogOpen] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [currentView, setCurrentView] = useState<'home' | 'project'>('home');
  const [projectId, setProjectId] = useState<string | null>(null);
  const projects = useProjectStore((state) => state.projects);
  const deleteProject = useProjectStore((state) => state.deleteProject);
  const setProjects = useProjectStore((state) => state.setProjects);

  // Sync projects from backend on mount
  useEffect(() => {
    syncProjects();
  }, []);

  const syncProjects = async () => {
    setIsSyncing(true);
    try {
      const backendProjects = await api.getProjects();
      if (backendProjects && backendProjects.length > 0) {
        setProjects(backendProjects);
      }
    } catch (error) {
      console.error("Failed to sync projects from backend:", error);
    } finally {
      setIsSyncing(false);
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

    <main className="relative h-screen w-screen bg-background flex flex-col">
      {/* Background Canvas */}
      <div className="fixed inset-0 z-0 pointer-events-none">
        <CreativeCanvas />
      </div>

      {/* Scrollable Content */}
      <div className="relative z-10 flex-1 overflow-y-auto">
        <div className="container mx-auto px-6 py-8">
          {/* Header with LumenX Branding */}
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-8"
          >
            <div className="flex gap-4 items-center">
              {/* Logo */}
              <div className="flex-shrink-0">
                <img
                  src="/static/LumenX.png"
                  alt="LumenX"
                  className="w-16 h-16 object-contain"
                />
              </div>

              {/* LumenX / Studio - Matching PipelineSidebar style */}
              <div className="flex flex-col gap-1">
                {/* LumenX (Top) */}
                <div className="flex items-center -mb-2">
                  <span className="font-display text-3xl font-bold tracking-tight text-primary">
                    Lumen
                  </span>
                  <span
                    className="font-display text-4xl font-black tracking-tighter ml-1"
                    style={{
                      background: 'linear-gradient(135deg, #a855f7 0%, #6366f1 50%, #ec4899 100%)',
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                      backgroundClip: 'text',
                    }}
                  >
                    X
                  </span>
                </div>

                {/* Studio (Bottom Right aligned) */}
                <div className="flex justify-end -mt-1 pl-9">
                  <span className="font-display text-3xl font-bold tracking-tight text-white">
                    Studio
                  </span>
                </div>
            <button
              onClick={() => setIsEnvDialogOpen(true)}
              className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors"
              title="配置环境变量"
            >
              <Settings size={20} />
              配置
            </button>
              </div>
            </div>

            {/* Slogan */}
            <p className="text-xs text-gray-500 tracking-wide mt-3 ml-10">
              Render Noise into Narrative
            </p>
          </motion.div>

          {/* Projects Section */}
          {projects.length === 0 ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-20"
            >
              <FolderOpen size={64} className="text-gray-600 mb-4" />
              <h3 className="text-xl font-medium text-gray-400 mb-2">还没有项目</h3>
              <p className="text-gray-500 mb-6">创建第一个项目开始吧！</p>
              <div className="flex gap-3">
                <button
                  onClick={() => setIsDialogOpen(true)}
                  className="bg-primary hover:bg-primary/90 text-white px-6 py-3 rounded-lg font-medium flex items-center gap-2 transition-colors"
                >
                  <Plus size={20} />
                  创建新项目
                </button>
                <button
                  onClick={syncProjects}
                  disabled={isSyncing}
                  className="bg-white/10 hover:bg-white/20 text-white px-6 py-3 rounded-lg font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
                >
                  <RefreshCw size={20} className={isSyncing ? "animate-spin" : ""} />
                  从后端同步
                </button>
              </div>
            </motion.div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-display font-bold text-white">
                  我的项目 ({projects.length})
                </h2>
                <div className="flex gap-3">
                  <button
                    onClick={syncProjects}
                    disabled={isSyncing}
                    className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm disabled:opacity-50"
                  >
                    <RefreshCw size={16} className={isSyncing ? "animate-spin" : ""} />
                    同步
                  </button>
                  <button
                    onClick={() => setIsDialogOpen(true)}
                    className="bg-primary hover:bg-primary/90 text-white px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors text-sm"
                  >
                    <Plus size={16} />
                    新建项目
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 pb-12">
                {projects.map((project, i) => (
                  <motion.div
                    key={project.id}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: Math.min(i * 0.03, 0.3) }}
                  >
                    <ProjectCard project={project} onDelete={deleteProject} />
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Create Project Dialog */}
      <CreateProjectDialog
        isOpen={isDialogOpen}
        onClose={() => setIsDialogOpen(false)}
      />

      {/* Environment Configuration Dialog */}
      <EnvConfigDialog
        isOpen={isEnvDialogOpen}
        onClose={() => setIsEnvDialogOpen(false)}
        isRequired={false}
      />
    </main>
  );
}
