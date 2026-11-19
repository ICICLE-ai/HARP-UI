from PipelineModule import PipelineModule

import os
import re
import pandas as pd

PIPELINE_HOME_VAR = "PIPELINE_HOME"

class DataScraper(PipelineModule):
    def __init__(self, config):
        super().__init__()
        self.application = str.lower(config["application"])
        self.application_category = str.lower(config["application_category"])
        self.config = config

    def get_csv_number(self, train_path):
        number = -1
        file_name_list = os.listdir(train_path)
        for file_name in file_name_list:
            if file_name[-4:] == ".csv": #get number
                name = file_name.split('.')[-2]
                number = max(number, int(name.split("_")[-1]))
        
        return number + 1

    def write_csv(self, dataframe):
        application_config = self.config["application_config"]
        train_folder_path = application_config["train_folder"]

        csv_name = "dataset_" + str(self.get_csv_number(train_folder_path)) + ".csv"
        csv_path = os.path.join(train_folder_path, csv_name)

        dataframe.to_csv(csv_path, header=True, index=False)


    def scrape_dnns(self):
        regex = re.compile('dnn_tmp_[0-9]+.pkl')
        tmp_files_path = os.getenv(PIPELINE_HOME_VAR)
        legacy = [name for name in os.listdir(tmp_files_path) if regex.fullmatch(name)]
        if legacy:
            raise RuntimeError(
                "legacy DNN scrape pickle files are not loaded because pickle can execute code; "
                "regenerate this profiling data in a non-pickle format"
            )
        raise RuntimeError("no supported DNN scrape data was found")
        

    def finalize(self):
        regex = re.compile('_tmp_[0-9]+.pkl')
        tmp_files_path = os.getenv(PIPELINE_HOME_VAR)
        print("INFO: REMOVING TMP FILES...")
        for file_name in os.listdir(tmp_files_path):
            if regex.search(file_name) != None:
                os.remove(os.path.join(tmp_files_path, file_name))


    def execute(self):
        if self.application_category == "trimmomatic":
            print(f"Advanced DataScraper module is not required for {self.application} under {self.application_category}")
        if self.application_category == "basic":
            print(f"Advanced DataScraper module is not required for {self.application} under {self.application_category}")
        elif self.application_category == "dnn":
            dataframe = self.scrape_dnns()
            self.write_csv(dataframe)
        else:
            print(f"ERROR: Application `{self.application}` was not found by DataScraper.")
            exit()

        self.finalize()
        print("INFO: Advanced DataScraper completed successfully")


