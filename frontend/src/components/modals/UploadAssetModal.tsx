"use client";

import { useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import NextImage from "next/image";
import { X, Upload, Image as ImageIcon, User, Layout, Eye } from "lucide-react";
import { messages } from "@/lib/i18n";
import type { Project } from "@/store/projectStore";

interface UploadAssetModalProps {
    isOpen: boolean;
    onClose: () => void;
    assetId: string;
    assetType: "character" | "scene" | "prop";
    assetName: string;
    defaultDescription: string;
    scriptId: string;
    onUploadComplete: (updatedScript: Project) => void;
}

const uploadCopy = messages.uploadAssetModal;
const commonActions = messages.common.actions;

const UPLOAD_TYPES = {
    character: [
        {
            id: "full_body",
            label: uploadCopy.assetTypes.character.fullBody.label,
            icon: User,
            description: uploadCopy.assetTypes.character.fullBody.description,
        },
        {
            id: "head_shot",
            label: uploadCopy.assetTypes.character.headShot.label,
            icon: Eye,
            description: uploadCopy.assetTypes.character.headShot.description,
        },
        {
            id: "three_views",
            label: uploadCopy.assetTypes.character.threeViews.label,
            icon: Layout,
            description: uploadCopy.assetTypes.character.threeViews.description,
        },
    ],
    scene: [
        { id: "image", label: uploadCopy.assetTypes.scene.image.label, icon: ImageIcon, description: uploadCopy.assetTypes.scene.image.description },
    ],
    prop: [
        { id: "image", label: uploadCopy.assetTypes.prop.image.label, icon: ImageIcon, description: uploadCopy.assetTypes.prop.image.description },
    ],
};

export default function UploadAssetModal({
    isOpen,
    onClose,
    assetId,
    assetType,
    assetName,
    defaultDescription,
    scriptId,
    onUploadComplete,
}: UploadAssetModalProps) {
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [uploadType, setUploadType] = useState<string>(
        assetType === "character" ? "full_body" : "image"
    );
    const [description, setDescription] = useState(defaultDescription);
    const [isUploading, setIsUploading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const validateImageFile = useCallback((file: File) => {
        if (!file.type.startsWith("image/")) {
            return uploadCopy.invalidImageFile;
        }
        if (file.size > 10 * 1024 * 1024) {
            return uploadCopy.fileTooLarge;
        }
        return null;
    }, []);

    const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            const validationError = validateImageFile(file);
            if (validationError) {
                setError(validationError);
                return;
            }
            setSelectedFile(file);
            setPreviewUrl(URL.createObjectURL(file));
            setError(null);
        }
    }, [validateImageFile]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        const file = e.dataTransfer.files?.[0];
        if (!file) return;

        const validationError = validateImageFile(file);
        if (validationError) {
            setError(validationError);
            return;
        }

        setSelectedFile(file);
        setPreviewUrl(URL.createObjectURL(file));
        setError(null);
    }, [validateImageFile]);

    const handleUpload = async () => {
        if (!selectedFile) {
            setError(uploadCopy.chooseImageFirst);
            return;
        }

        setIsUploading(true);
        setError(null);

        try {
            // Use api.uploadAsset which uses the correct backend API URL
            const { api } = await import("@/lib/api");
            const updatedScript = await api.uploadAsset(
                scriptId,
                assetType,
                assetId,
                selectedFile,
                uploadType,
                description
            );
            onUploadComplete(updatedScript);
            handleClose();
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : uploadCopy.uploadFailed);
        } finally {
            setIsUploading(false);
        }
    };

    const handleClose = () => {
        setSelectedFile(null);
        setPreviewUrl(null);
        setError(null);
        setDescription(defaultDescription);
        onClose();
    };

    const uploadTypes = UPLOAD_TYPES[assetType] || [];
    const descriptionLabel = uploadCopy.descriptionLabelByType[assetType];
    const descriptionPlaceholder = uploadCopy.descriptionPlaceholderByType[assetType];

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                onClick={handleClose}
            >
                <motion.div
                    initial={{ scale: 0.95, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.95, opacity: 0 }}
                    className="bg-gray-900 rounded-xl p-6 w-full max-w-lg mx-4 shadow-2xl border border-white/10"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between mb-6">
                        <h2 className="text-xl font-bold text-white">
                            {uploadCopy.title(assetName)}
                        </h2>
                        <button
                            onClick={handleClose}
                            className="p-2 hover:bg-white/10 rounded-lg transition-colors"
                        >
                            <X size={20} className="text-gray-400" />
                        </button>
                    </div>

                    {/* Upload Type Selector (only for Character) */}
                    {assetType === "character" && (
                        <div className="mb-6">
                            <label className="block text-sm font-medium text-gray-400 mb-3">
                                {uploadCopy.assetTypeLabel}
                            </label>
                            <div className="grid grid-cols-3 gap-3">
                                {uploadTypes.map((type) => {
                                    const Icon = type.icon;
                                    return (
                                        <button
                                            key={type.id}
                                            onClick={() => setUploadType(type.id)}
                                            className={`p-4 rounded-lg border-2 transition-all ${uploadType === type.id
                                                ? "border-primary bg-primary/10"
                                                : "border-white/10 hover:border-white/20"
                                                }`}
                                        >
                                            <Icon
                                                size={24}
                                                className={`mx-auto mb-2 ${uploadType === type.id ? "text-primary" : "text-gray-400"
                                                    }`}
                                            />
                                            <div className="text-sm font-medium text-white">{type.label}</div>
                                            <div className="text-xs text-gray-500 mt-1">{type.description}</div>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {/* File Upload Area */}
                    <div className="mb-6">
                        <label className="block text-sm font-medium text-gray-400 mb-3">
                            {uploadCopy.selectImage}
                        </label>
                        <div
                            onDrop={handleDrop}
                            onDragOver={(e) => e.preventDefault()}
                            onClick={() => fileInputRef.current?.click()}
                            className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-all ${previewUrl
                                ? "border-primary bg-primary/5"
                                : "border-white/20 hover:border-white/40"
                                }`}
                        >
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="image/*"
                                onChange={handleFileSelect}
                                className="hidden"
                            />
                            {previewUrl ? (
                                <div className="relative">
                                    <NextImage
                                        src={previewUrl}
                                        alt={uploadCopy.imagePreviewAlt}
                                        width={384}
                                        height={192}
                                        unoptimized
                                        className="max-h-48 mx-auto rounded-lg object-contain"
                                    />
                                    <div className="mt-3 text-sm text-gray-400">
                                        {uploadCopy.replaceImage}
                                    </div>
                                </div>
                            ) : (
                                <>
                                    <Upload size={32} className="mx-auto text-gray-500 mb-3" />
                                    <div className="text-gray-400">{uploadCopy.dragOrClick}</div>
                                    <div className="text-xs text-gray-500 mt-2">
                                        {uploadCopy.supportedFormats}
                                    </div>
                                </>
                            )}
                        </div>
                    </div>

                    {/* Description Editor */}
                    <div className="mb-6">
                        <label className="block text-sm font-medium text-gray-400 mb-2">
                            {descriptionLabel} <span className="text-xs text-gray-500">({uploadCopy.descriptionPreviewHint})</span>
                        </label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            rows={3}
                            className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white text-sm resize-none focus:outline-none focus:border-primary/50"
                            placeholder={descriptionPlaceholder}
                        />
                        <div className="text-xs text-gray-500 mt-1">
                            {uploadCopy.descriptionHint}
                        </div>
                    </div>

                    {/* Error Message */}
                    {error && (
                        <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
                            {error}
                        </div>
                    )}

                    {/* Actions */}
                    <div className="flex gap-3">
                        <button
                            onClick={handleClose}
                            className="flex-1 px-4 py-2 bg-white/5 hover:bg-white/10 text-white rounded-lg transition-colors"
                        >
                            {commonActions.cancel}
                        </button>
                        <button
                            onClick={handleUpload}
                            disabled={!selectedFile || isUploading}
                            className="flex-1 px-4 py-2 bg-primary hover:bg-primary/90 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                        >
                            {isUploading ? (
                                <>
                                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                    {uploadCopy.actions.uploading}
                                </>
                            ) : (
                                <>
                                    <Upload size={16} />
                                    {uploadCopy.actions.confirmUpload}
                                </>
                            )}
                        </button>
                    </div>
                </motion.div>
            </motion.div>
        </AnimatePresence>
    );
}
