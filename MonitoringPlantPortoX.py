import streamlit as st
from supabase import create_client
import pandas as pd
from datetime import date
from google import genai
from PIL import Image

# Konfigurasi halaman Streamlit
st.set_page_config(
    page_title="Porto X Factory System",
    page_icon="🩴",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inisialisasi Koneksi Supabase & Gemini SDK Baru
@st.cache_resource
def init_connections():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        supabase = create_client(url, key)
        
        # Menggunakan client genai yang baru
        ai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
        return supabase, ai_client
    except Exception as e:
        st.error(f"Gagal memuat koneksi: {e}")
        return None, None

supabase, ai_client = init_connections()

# Sidebar Navigasi
st.sidebar.title("🩴 Porto X Production")
menu = st.sidebar.selectbox("Pilih Menu", ["Form Setting Mesin (OCR + Manual)", "Dashboard Analisa Manager"])

if supabase is None or ai_client is None:
    st.warning("Mohon pastikan file `.streamlit/secrets.toml` sudah diisi dengan `SUPABASE_URL`, `SUPABASE_KEY`, dan `GEMINI_API_KEY`.")
else:
    if menu == "Form Setting Mesin (OCR + Manual)":
        st.header("📝 Laporan Setting Mesin EVA")
        st.markdown("Gunakan fitur **Scan OCR AI** atau isi manual parameter mesin di bawah ini.")

        # Ambil master data mesin
        try:
            machines_res = supabase.table("machines").select("id, code").eq("active", True).execute()
            machines = {m["code"]: m["id"] for m in machines_res.data} if machines_res and machines_res.data else {"M-06": 1, "M-09": 2}
        except Exception:
            machines = {"M-06": 1, "M-09": 2}

        # Ambil master SKU
        sku_options = {}
        try:
            skus_res = supabase.table("v_sku_pick").select("sku_id, sku_code, model_name, color_name, size_label, variant").execute()
            if skus_res and skus_res.data:
                sku_options = {f"{s['model_name']} - {s['color_name']} - Size {s['size_label']} ({s['variant']})": s['sku_id'] for s in skus_res.data}
        except Exception:
            pass
        
        if not sku_options:
            sku_options = {"Default SKU - Model A": 1}

        with st.form("setting_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                selected_machine_code = st.selectbox("Pilih Mesin", list(machines.keys()))
                machine_id = machines.get(selected_machine_code, 1)
            with col2:
                report_date = st.date_input("Tanggal Produksi", date.today())
            with col3:
                shift = st.selectbox("Shift", ["pagi", "malam"])

            col4, col5, col6 = st.columns(3)
            with col4:
                team = st.selectbox("Tim", ["A", "B", "C"])
            with col5:
                report_type = st.selectbox("Jenis Laporan", ["awal", "akhir", "perubahan"])
            with col6:
                change_reason = st.selectbox("Alasan Perubahan", [None, "ganti_bahan", "ganti_warna_tipe", "bs_tinggi", "trial", "lainnya"])

            material_mix = st.text_input("Campuran Bahan (Contoh: L60:S40)", "L60:S40")
            
            st.markdown("---")
            st.subheader("📸 Scan Cepat Layar HMI (AI OCR)")
            uploaded_file = st.file_uploader("Upload Foto Layar HMI (Opsional)", type=["jpg", "jpeg", "png"])
            
            if uploaded_file is not None:
                image = Image.open(uploaded_file)
                st.image(image, caption="Foto HMI yang diunggah", use_container_width=True)
                if st.form_submit_button("🤖 Proses Baca Angka dengan AI OCR"):
                    with st.spinner("AI sedang membaca parameter HMI..."):
                        try:
                            response = ai_client.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=[image, "Ekstrak seluruh angka parameter suhu dan setting pintu dari gambar layar HMI mesin ini ke dalam format JSON."]
                            )
                            st.success("Berhasil dibaca oleh AI!")
                            st.code(response.text, language="json")
                        except Exception as e:
                            st.error(f"Gagal OCR: {e}")

            st.markdown("---")
            st.subheader("Pengaturan Pintu Cetakan (1 - 6)")
            
            doors_data = []
            for d_no in range(1, 7):
                with st.expander(f"Pintu Stasiun #{d_no}", expanded=(d_no == 1)):
                    is_idle = st.checkbox(f"Pintu #{d_no} Idle (Tidak Jalan)", key=f"idle_{d_no}")
                    if not is_idle:
                        col_sku, col_side, col_suu, col_sbl = st.columns(4)
                        with col_sku:
                            chosen_sku_label = st.selectbox(f"SKU Pintu {d_no}", list(sku_options.keys()), key=f"sku_{d_no}")
                            sku_id = sku_options.get(chosen_sku_label, 1)
                        with col_side:
                            side = st.selectbox(f"Sisi {d_no}", ["kanan", "kiri"], key=f"side_{d_no}")
                        with col_suu:
                            sv_upper = st.number_input(f"SV Atas (°C) #{d_no}", value=189.0, key=f"svu_{d_no}")
                        with col_sbl:
                            sv_lower = st.number_input(f"SV Bawah (°C) #{d_no}", value=175.0, key=f"svl_{d_no}")
                        
                        col_pv1, col_pv2 = st.columns(2)
                        with col_pv1:
                            pv_upper_ok = st.checkbox(f"PV Atas Sesuai (±3°) #{d_no}", value=True, key=f"pvu_{d_no}")
                        with col_pv2:
                            pv_lower_ok = st.checkbox(f"PV Bawah Sesuai (±3°) #{d_no}", value=True, key=f"pvl_{d_no}")

                        doors_data.append({
                            "door_no": d_no,
                            "idle": False,
                            "sku_id": sku_id,
                            "side": side,
                            "sv_upper": sv_upper,
                            "sv_lower": sv_lower,
                            "pv_upper_ok": pv_upper_ok,
                            "pv_lower_ok": pv_lower_ok
                        })
                    else:
                        doors_data.append({"door_no": d_no, "idle": True})

            submitted = st.form_submit_button("Simpan Laporan Setting")
            if submitted:
                report_meta = {
                    "machine_id": machine_id,
                    "report_date": str(report_date),
                    "shift": shift,
                    "team": team,
                    "report_type": report_type,
                    "change_reason": change_reason if report_type == "perubahan" else None,
                    "material_mix": material_mix,
                }
                try:
                    supabase.rpc("submit_setting_report", {
                        "p_report": report_meta,
                        "p_values": [],
                        "p_doors": doors_data
                    }).execute()
                    st.success("Laporan setting berhasil disimpan ke database!")
                except Exception as e:
                    st.error(f"Gagal menyimpan laporan: {e}")

    elif menu == "Dashboard Analisa Manager":
        st.header("📊 Dashboard Analisa Produksi Porto X")
        st.markdown("Pantau aktivitas mesin dan riwayat laporan secara *real-time*.")
        try:
            reports_res = supabase.table("setting_reports").select("id, machine_id, report_date, shift, team, report_type, material_mix, created_at").order("created_at", desc=True).limit(10).execute()
            if reports_res.data:
                df_reports = pd.DataFrame(reports_res.data)
                st.dataframe(df_reports, use_container_width=True)
            else:
                st.info("Belum ada data laporan yang masuk.")
        except Exception as e:
            st.info("Tabel riwayat belum tersedia atau koneksi belum lengkap.")
