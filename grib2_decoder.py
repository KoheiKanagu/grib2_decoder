import os.path
import subprocess
import re
import pandas as pd

from enum import Enum
from PIL import Image, ImageDraw
from datetime import datetime
from tqdm import tqdm


class Loader():
    _df: pd.DataFrame
    _wgrib2_path: str
    _file_name: str

    def __init__(self, wgrib2_path: str, file_name: str):
        """
        `./wgrib2 FILE_grib2.bin -grib` の出力をパース
        """
        self._wgrib2_path = wgrib2_path
        self._file_name = file_name

        out = subprocess.Popen(
            # str(os.getenv("WGRIB2")) + " " + file_name,
            f"{wgrib2_path} {file_name} -grid",
            stdout=subprocess.PIPE,
            shell=True
        ).stdout.read().decode("utf-8")

        reg = re.compile(
            r"(.*):0:grid_template=0:winds\(N/S\):\n.*lat-lon grid:\((.*) x .*\) units 1e-06 input WE:NS output WE:SN res .*\n.*lat (.*) to (.*) by .*\n.*lon (.*) to (.*) by .* #points=.*")
        # https://regexper.com/#%28.*%29%3A0%3Agrid_template%3D0%3Awinds%5C%28N%5C%2FS%5C%29%3A%5Cn.*lat-lon%20grid%3A%5C%28%28.*%29%20x%20.*%5C%29%20units%201e-06%20input%20WE%3ANS%20output%20WE%3ASN%20res%20.*%5Cn.*lat%20%28.*%29%20to%20%28.*%29%20by%20.*%5Cn.*lon%20%28.*%29%20to%20%28.*%29%20by%20.*%20%23points%3D.*

        self._df = pd.DataFrame(
            reg.findall(out),
            columns=["id", "grid", "lat1", "lat2", "lon1", "lon2"]
        ).astype(
            {"grid": int, "lat1": float, "lat2": float, "lon1": float, "lon2": float}
        )
        # gridは何マスのグリッドで区切っているか。40なら40x40。160なら160x160。
        # lat1,lon1 はグリッドの北西。lat2,lon2 はグリッドの南東

    def _read_fcst(self)->pd.DataFrame:
        """
        `./wgrib2 FILE_grib2.bin` の出力からgridのidとfcstをパース
        """
        out = self._read_console_out(f"{self._wgrib2_path} {self._file_name}")

        reg = re.compile(r"(.*):0:.*:surface:(.*):")
        fcst = pd.DataFrame(
            reg.findall(out),
            columns=["id", "fcst"]
        )
        return fcst

    def _read_console_out(self, cmd: str):
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            shell=True
        ).stdout.read().decode("utf-8")

    def _read_console_out_lines(self, cmd: str):
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            shell=True
        ).stdout.readlines()

    def _around_grid(self, lat: float, lon: float, mag: float) -> pd.DataFrame:
        _lat1 = self._df["lat1"]
        _lon1 = self._df["lon1"]
        _lat2 = self._df["lat2"]
        _lon2 = self._df["lon2"]

        return self._df[
            ((_lat1 > lat) | (_lat1 > lat+mag) | (_lat1 > lat-mag)) &
            ((_lat2 < lat) | (_lat2 < lat+mag) | (_lat2 < lat-mag)) &
            ((_lon1 < lon) | (_lon1 < lon+mag) | (_lon1 < lon-mag)) &
            ((_lon2 > lon) | (_lon2 > lon+mag) | (_lon2 > lon-mag))
        ]

    def get_grid(self, lat: float, lon: float, mag=float("inf")) -> pd.DataFrame:
        """
        fcstの情報付きのgridを作成
        magは捜索範囲。latとlonにプラスする。指定なしだと日本全土
        """
        grid = self._around_grid(lat, lon, mag)
        fcst = self._read_fcst()

        return grid.join(fcst[fcst["id"].isin(grid["id"])]["fcst"])

    def get_rainfall(self, grid_ids: iter) -> pd.DataFrame:
        """
        雨量情報を取得
        ****ここでの時間はUTC****
        """
        datas = []

        for d in tqdm(grid_ids):
            csv = self._read_console_out_lines(
                f"{self._wgrib2_path} {self._file_name} -d {d} -csv -"
            )

            # ヘッダとフッタは不要なので削除
            del csv[0]
            del csv[-1]

            df = pd.DataFrame(
                data=[line.decode("utf-8").split(",") for line in csv]
            )
            df[0] = d  # [0]は使わないのでidを入れて使い回す
            df[1] = df[1].apply(
                lambda s: datetime.strptime(
                    s.replace('"', ""), "%Y-%m-%d %H:%M:%S")
            )
            df[6] = df[6].apply(
                lambda s: float(s.replace("\n", ""))
            )
            df = df.rename(
                columns={0: "id", 1: "datetime", 4: "lon", 5: "lat", 6: "val"}
            )
            datas.append(df)
        return pd.concat(datas)


class Visualize():
    @staticmethod
    def _to_color(value) -> str:
        # https://www.jma.go.jp/jp/kaikotan/
        if value >= 80:
            return "#a62366"
        elif value >= 50:
            return "#ec3d25"
        elif value >= 30:
            return "#f29d39"
        elif value >= 20:
            return "#fdf451"
        elif value >= 10:
            return "#133cf5"
        elif value >= 5:
            return "#418cf7"
        elif value >= 1:
            return "#aad1fb"
        else:
            return "#f2f2fe"

    @staticmethod
    def _to_color_rgb(value) -> (int, int, int):
        h = Visualize._to_color(value).lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def get_concat_h(img1: Image, img2: Image):
        # https://note.nkmk.me/python-pillow-concat-images/
        dst = Image.new('RGB', (img1.width + img2.width, img1.height))
        dst.paste(img1, (0, 0))
        dst.paste(img2, (img1.width, 0))
        return dst

    @staticmethod
    def get_concat_v(img1: Image, img2: Image):
        dst = Image.new('RGB', (img1.width, img1.height + img2.height))
        dst.paste(img1, (0, 0))
        dst.paste(img2, (0, img1.height))
        return dst

    @staticmethod
    def to_image(grid_size, rainfall_val, mag=1):
        img = Image.new("RGB", (grid_size, grid_size))
        draw = ImageDraw.Draw(img)

        for i, val in enumerate(rainfall_val):
            x = int((i+1) % grid_size)
            y = int(i / grid_size)
            # 画像を反転
            y = (y-(int(grid_size/2)-1)) * -1 + int(grid_size/2)
            draw.point(
                (x, y),
                fill=Visualize._to_color_rgb(val)
            )
        return img.resize((grid_size*mag, grid_size*mag))

    @staticmethod
    def save_images(target_grid_info: pd.DataFrame, rainfall: pd.DataFrame, save_dir="./map", mag=1):
        """
        magは画像の倍率。
        1だとgrid*gridの一対一
        """
        for i in tqdm(target_grid_info["id"]):
            grid = target_grid_info[target_grid_info["id"] == i]["grid"]
            plot_d = rainfall[rainfall["id"] == i]
            img = Visualize.to_image(grid.values[0], plot_d["val"], mag)
            name = i.replace(".", "")
            file = os.path.join(save_dir, f"{name}.png")
            img.save(file, "PNG")

    @staticmethod
    def save_csv(df: pd.DataFrame, opacity=0.8, file_name="./map/out.csv"):
        df[["lat1", "lon1", "lat2", "lon2"]].join(
            df["id"].apply(lambda s: str(s).replace(".", ""))
        ).reset_index(drop=True)\
            .join(
            pd.Series([str(opacity)]*len(df), name="opacity")
        ).loc[:, ["id", "lat1", "lon1", "lat2", "lon2", "opacity"]].to_csv(file_name)


class FCST(Enum):
    MIN_minus5 = "-5 min fcst"
    ANL = "anl"
    MIN_5 = "5 min fcst"
    MIN_10 = "10 min fcst"
    MIN_15 = "15 min fcst"
    MIN_20 = "20 min fcst"
    MIN_25 = "25 min fcst"
    MIN_30 = "30 min fcst"
    MIN_35 = "35 min fcst"
    MIN_40 = "40 min fcst"
    MIN_45 = "45 min fcst"
    MIN_50 = "50 min fcst"
    MIN_55 = "55 min fcst"
